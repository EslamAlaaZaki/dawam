from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, get_args

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.config import ConfigError, Settings
from dawam.platform.email import Delivery, EmailMessage, Mailer, OneTimeLink
from dawam.platform.errors import ApiError

from .internal.credentials import (
    display_name_problem,
    is_valid_email,
    normalize_display_name,
    normalize_email,
    password_problem,
)
from .internal.login_guard import LoginGuard, too_many_attempts, too_many_sign_ups
from .internal.security_events import SecurityEventRecorder
from .tables import (
    IP_MAX_LENGTH,
    USER_AGENT_MAX_LENGTH,
    PasswordResetRecord,
    SessionRecord,
    UserRecord,
)

logger = logging.getLogger(__name__)

SystemRole = Literal["admin", "user"]
_SYSTEM_ROLES: frozenset[str] = frozenset(get_args(SystemRole))

# A session's last_seen_at is written at most this often, not on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)

# Serialises bootstrap-admin creation when several app replicas start at once.
_BOOTSTRAP_LOCK_KEY = 0x0DA3A4

# argon2-cffi's defaults: argon2id with the RFC 9106 low-memory parameters.
_hasher = PasswordHasher()

# Verified against when the email is unknown, so both cases take as long. Computed at
# import, so the first such sign-in is not slower than the rest.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _email_taken() -> ApiError:
    return ApiError(409, "email_taken", "A user with this email already exists.")


@contextmanager
def _email_must_be_free() -> Iterator[None]:
    """Turn the unique-email violation of an insert into ``409 email_taken``. The
    constraint, not a pre-check, decides: two concurrent creates of one email cannot
    both succeed."""
    try:
        yield
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_users_email":
            raise _email_taken() from None
        raise


def _account_deactivated() -> ApiError:
    return ApiError(403, "account_deactivated", "This account has been deactivated.")


def _checked_password(password: str) -> str:
    problem = password_problem(password)
    if problem:
        raise ApiError(422, "invalid_password", f"The password {problem}.")
    return password


def _checked_display_name(display_name: str) -> str:
    display_name = normalize_display_name(display_name)
    problem = display_name_problem(display_name)
    if problem:
        raise ApiError(422, "invalid_display_name", f"The display name {problem}.")
    return display_name


def _new_user_record(
    email: str,
    password: str,
    display_name: str,
    system_role: SystemRole,
    *,
    now: datetime,
    must_change_password: bool = False,
) -> UserRecord:
    """A new user's row, checked. Raises ``ApiError`` 422 ``invalid_email``,
    ``invalid_display_name`` or ``invalid_password``."""
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ApiError(422, "invalid_email", "The email address is not valid.")
    display_name = _checked_display_name(display_name)
    return UserRecord(
        email=email,
        display_name=display_name,
        password_hash=_hasher.hash(_checked_password(password)),
        system_role=system_role,
        is_active=True,
        must_change_password=must_change_password,
        created_at=now,
    )


@dataclass(frozen=True)
class RegistrationRules:
    """Who may sign up on their own (spec stories 1, 20): nobody unless ``open``; then,
    if ``allowed_email_domains`` is not empty, only emails at exactly one of them."""

    open: bool = False
    allowed_email_domains: frozenset[str] = frozenset()

    def allows(self, email: str) -> bool:
        """Whether a normalised email may sign up."""
        domain = email.rpartition("@")[2]
        return self.open and (
            not self.allowed_email_domains or domain in self.allowed_email_domains
        )


_CLOSED = RegistrationRules()


class RegistrationPolicy(Protocol):
    """Where the registration rules come from. The auth module does not keep them: the
    admin module's system settings implement this, and the composition root hands them
    to auth."""

    def registration_rules(self) -> RegistrationRules:
        """The rules in force now."""
        ...


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    email: str
    display_name: str
    system_role: SystemRole
    last_login_at: datetime | None
    is_active: bool = True
    must_change_password: bool = False
    """Set for a user an admin created with a temporary password, until they change it."""
    created_at: datetime | None = None


@dataclass(frozen=True)
class SessionInfo:
    """What DAWAM knows about one signed-in browser (never its token)."""

    ip: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class SignedIn:
    """A new session: ``token`` goes into the session cookie and nowhere else."""

    token: str
    user: User


def _user(record: UserRecord) -> User:
    if record.system_role not in _SYSTEM_ROLES:
        raise ValueError(f"user {record.id} has an unknown system role {record.system_role!r}")
    return User(
        id=record.id,
        email=record.email,
        display_name=record.display_name,
        system_role=record.system_role,  # type: ignore[arg-type]  # checked above
        last_login_at=record.last_login_at,
        is_active=record.is_active,
        must_change_password=record.must_change_password,
        created_at=record.created_at,
    )


def _start_session(
    db: Session,
    user: UserRecord,
    password: str,
    now: datetime,
    *,
    lifetime: timedelta,
    replacing: str | None,
    ip: str | None,
    user_agent: str | None,
) -> SignedIn:
    """Sign ``user`` in, having just checked or set ``password``: a new session that
    ends ``lifetime`` from ``now``, replacing the browser's session ``replacing``."""
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    user.last_login_at = now
    if replacing:
        db.execute(
            sa.delete(SessionRecord).where(SessionRecord.token_hash == _token_hash(replacing))
        )
    token = secrets.token_urlsafe(32)
    db.add(
        SessionRecord(
            token_hash=_token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
            ip=ip[:IP_MAX_LENGTH] if ip else None,
            user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
        )
    )
    return SignedIn(token=token, user=_user(user))


class AuthService:
    """Users, passwords and server-side sessions.

    A session is identified by a random token that only the browser's cookie holds;
    the ``sessions`` row stores its SHA-256 (``token_hash``), not the token, so a
    database leak yields no usable sessions. (Spec §7 speaks of the session ID in the
    cookie; the row's ``id`` is never sent.)
    """

    def __init__(self, engine: sa.Engine, settings: Settings, *, clock: Clock) -> None:
        self._engine = engine
        self._settings = settings
        self._clock = clock
        self._events = SecurityEventRecorder(engine, clock=clock)
        self._guard = LoginGuard(settings, self._events)

    @property
    def idle_timeout(self) -> timedelta:
        return timedelta(hours=self._settings.session_idle_timeout_hours)

    @property
    def absolute_timeout(self) -> timedelta:
        return timedelta(days=self._settings.session_absolute_timeout_days)

    def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        system_role: SystemRole = "user",
    ) -> User:
        """Create a user. Raises ``ApiError``: 422 ``invalid_email`` or
        ``invalid_password``, 409 ``email_taken``."""
        record = self._new_user(email, password, display_name, system_role)
        with _email_must_be_free(), Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            return _user(record)

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        policy: RegistrationPolicy | None,
        replacing: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SignedIn:
        """Self-registration: create a regular user and sign them in, if ``policy``'s
        rules (read once; no policy: closed) allow it.

        Raises ``ApiError``: 403 ``registration_closed`` or ``email_domain_not_allowed``,
        429 ``too_many_attempts`` (too many sign-ups from ``ip``;
        ``internal.login_guard``), 422 ``invalid_email``, ``invalid_display_name`` or
        ``invalid_password``, 409 ``email_taken``. ``replacing``, ``ip`` and
        ``user_agent`` are as for ``sign_in``. Records a ``user_registered`` security
        event."""
        rules = policy.registration_rules() if policy else _CLOSED
        if not rules.open:
            raise ApiError(403, "registration_closed", "Self-registration is turned off.")
        now = self._clock()
        # Every attempt from an address counts, committed before anything is checked or
        # hashed, so a refused or failing one costs the address too.
        with Session(self._engine) as db, db.begin():
            throttled_until = self._guard.sign_up_throttled_until(db, ip, now)
            if throttled_until is None:
                self._guard.sign_up_attempted(db, ip, now)
        if throttled_until is not None:
            logger.warning("sign-up throttled", extra={"ip": ip})
            raise too_many_sign_ups(throttled_until, now)
        normalized = normalize_email(email)
        if is_valid_email(normalized) and not rules.allows(normalized):
            raise ApiError(
                403,
                "email_domain_not_allowed",
                "Self-registration is not open to this email domain.",
            )
        record = self._new_user(normalized, password, display_name, "user")
        with _email_must_be_free(), Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            self._events.record(
                "user_registered",
                actor_id=record.id,
                target_type="user",
                target_id=record.id,
                metadata={"email": record.email},
                ip=ip,
                db=db,
            )
            return self._start_session(db, record, password, now, replacing, ip, user_agent)

    def get_user(self, user_id: uuid.UUID) -> User | None:
        with Session(self._engine) as db:
            record = db.get(UserRecord, user_id)
            return _user(record) if record else None

    def sessions_of(self, user_id: uuid.UUID) -> list[SessionInfo]:
        """The user's sessions, oldest first (expired ones may still be listed)."""
        with Session(self._engine) as db:
            records = db.scalars(
                sa.select(SessionRecord)
                .where(SessionRecord.user_id == user_id)
                .order_by(SessionRecord.created_at)
            )
            return [
                SessionInfo(
                    ip=r.ip,
                    user_agent=r.user_agent,
                    created_at=r.created_at,
                    last_seen_at=r.last_seen_at,
                )
                for r in records
            ]

    def ensure_bootstrap_admin(self) -> None:
        """Create the admin from ``DAWAM_ADMIN_EMAIL``/``DAWAM_ADMIN_PASSWORD`` if they are
        set and no admin exists. Never changes an existing user.

        Only when it is about to create the admin does it check the values; it raises
        ``ConfigError`` (naming the variable, never echoing the password) if only one
        is set, the email is not an address, the password breaks the policy, or a
        non-admin user already has the email. Once an admin exists, stale or partial
        values never stop startup."""
        admin_email, admin_password = self._settings.admin_email, self._settings.admin_password
        if admin_email is None and admin_password is None:
            return
        with Session(self._engine) as db, db.begin():
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(_BOOTSTRAP_LOCK_KEY)))
            if db.scalar(sa.select(sa.exists().where(UserRecord.system_role == "admin"))):
                return
            if admin_email is None or admin_password is None:
                raise ConfigError(
                    "No admin exists yet: set both DAWAM_ADMIN_EMAIL and "
                    "DAWAM_ADMIN_PASSWORD to create one, or neither."
                )
            email = normalize_email(admin_email)
            password = admin_password.get_secret_value()
            if not is_valid_email(email):
                raise ConfigError("DAWAM_ADMIN_EMAIL is invalid: must be an email address.")
            problem = password_problem(password)
            if problem:
                raise ConfigError(f"DAWAM_ADMIN_PASSWORD is invalid: {problem}.")
            if db.scalar(sa.select(sa.exists().where(UserRecord.email == email))):
                raise ConfigError(
                    "DAWAM_ADMIN_EMAIL belongs to an existing user who is not an admin, and "
                    "no admin exists yet. DAWAM leaves that user alone: set "
                    "DAWAM_ADMIN_EMAIL to an address no user has."
                )
            db.add(self._new_user(email, password, "Administrator", "admin"))
        logger.info("bootstrap admin created")

    def sign_in(
        self,
        email: str,
        password: str,
        *,
        replacing: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SignedIn:
        """Check the credentials and start a session. ``replacing`` is the token of the
        browser's current session, if any, which is ended. ``ip`` and ``user_agent`` are
        recorded on the session.

        Raises ``ApiError`` 401 ``invalid_credentials``, or 429 ``account_locked`` once
        too many consecutive attempts on the account have failed, or 429
        ``too_many_attempts`` once too many from ``ip`` have (``internal.login_guard``). Every
        attempt is recorded as a security event (``login_succeeded`` or
        ``login_failed``, plus ``account_locked`` when it locks the account)."""
        now = self._clock()
        email = normalize_email(email)
        with Session(self._engine) as db:
            throttled_until = self._guard.throttled_until(db, ip, now)
        if throttled_until is not None:
            error = too_many_attempts(throttled_until, now)
            # Not a security event (an address may send many); a log line per attempt.
            logger.warning(
                "sign-in throttled",
                extra={"ip": ip, "retry_after_seconds": error.details["retry_after_seconds"]},
            )
            raise error
        with Session(self._engine) as db, db.begin():
            # Locked, so concurrent attempts on one account are counted one at a time.
            user = db.scalar(
                sa.select(UserRecord).where(UserRecord.email == email).with_for_update()
            )
            state = self._guard.state_of(db, user, email, now)
            locked_until = self._guard.locked_until(state, now)
            # A locked email is refused without checking the password, account or not.
            correct = locked_until is None and self._verify(
                user.password_hash if user else _DUMMY_HASH, password
            )
            if correct and user is not None and not user.is_active:
                # Only someone who knows the password learns the account is deactivated.
                self._events.record(
                    "login_failed",
                    target_type="user",
                    target_id=user.id,
                    metadata={"email": email, "reason": "account_deactivated"},
                    ip=ip,
                    db=db,
                )
                failure = _account_deactivated()
            elif correct and user is not None:
                user.failed_login_count = 0  # a success ends the streak
                signed_in = self._start_session(db, user, password, now, replacing, ip, user_agent)
                self._events.record(
                    "login_succeeded",
                    actor_id=user.id,
                    target_type="user",
                    target_id=user.id,
                    metadata={"email": email},
                    ip=ip,
                    db=db,
                )
                return signed_in
            else:
                # Committed: a failure is recorded before it is raised.
                failure = self._guard.failed(
                    db, state, user, email, ip=ip, now=now, locked_until=locked_until
                )
        with Session(self._engine) as db, db.begin():
            self._guard.prune(db, now)
        raise failure

    def _start_session(
        self,
        db: Session,
        user: UserRecord,
        password: str,
        now: datetime,
        replacing: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> SignedIn:
        return _start_session(
            db,
            user,
            password,
            now,
            lifetime=self.absolute_timeout,
            replacing=replacing,
            ip=ip,
            user_agent=user_agent,
        )

    def user_for_session(self, token: str) -> User | None:
        """The signed-in user of a live session, or None. An expired session is deleted."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            row = db.execute(
                sa.select(SessionRecord, UserRecord)
                .join(UserRecord, UserRecord.id == SessionRecord.user_id)
                .where(SessionRecord.token_hash == _token_hash(token))
            ).first()
            if row is None:
                return None
            session, user = row
            if (
                not user.is_active  # deactivation deletes sessions; this is the backstop
                or now >= session.expires_at  # absolute timeout, fixed at sign-in
                or now >= session.last_seen_at + self.idle_timeout
            ):
                db.delete(session)
                return None
            if now - session.last_seen_at >= _TOUCH_INTERVAL:
                session.last_seen_at = now
            return _user(user)

    def update_display_name(self, user_id: uuid.UUID, display_name: str) -> User:
        """Change a user's display name (stored trimmed). Raises ``ApiError``: 422
        ``invalid_display_name``, 404 ``not_found``."""
        display_name = _checked_display_name(display_name)
        with Session(self._engine) as db, db.begin():
            user = self._existing_user(db, user_id)
            user.display_name = display_name
            return _user(user)

    def change_password(
        self,
        user_id: uuid.UUID,
        current_password: str,
        new_password: str,
        *,
        keep_session: str | None = None,
        ip: str | None = None,
    ) -> None:
        """Replace the user's password, given the current one, and end every session of
        theirs except the one whose token is ``keep_session`` (the browser asking), so a
        stolen session dies with the old password, and clear ``must_change_password``.
        Records a ``password_changed`` security event from ``ip``.

        A wrong current password counts against the account like a failed sign-in
        (and is recorded as ``password_change_failed``), so it cannot be guessed here
        either. Raises ``ApiError``: 400 ``wrong_password``, 429 ``account_locked``
        (when that locks the account, or it was locked), 422 ``invalid_password`` (or
        ``password_unchanged``: a temporary password kept as it is), 404 ``not_found``."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            user = self._existing_user(db, user_id)
            # Like a sign-in: a locked account is refused unchecked, and a wrong
            # password counts towards the lock (``internal.login_guard``).
            locked_until = self._guard.locked_until(user, now)
            if locked_until is None and self._verify(user.password_hash, current_password):
                user.failed_login_count = 0  # knowing the password ends the streak
                if user.must_change_password and new_password == current_password:
                    raise ApiError(
                        422,
                        "password_unchanged",
                        "Choose a new password, not the temporary one you were given.",
                    )
                user.password_hash = _hasher.hash(_checked_password(new_password))
                user.must_change_password = False
                others = sa.delete(SessionRecord).where(SessionRecord.user_id == user.id)
                if keep_session:
                    others = others.where(SessionRecord.token_hash != _token_hash(keep_session))
                ended = db.execute(others).rowcount
                self._events.record(
                    "password_changed",
                    actor_id=user.id,
                    target_type="user",
                    target_id=user.id,
                    metadata={"other_sessions_ended": ended},
                    ip=ip,
                    db=db,
                )
                return
            # Committed with the transaction: the failure is recorded before it is raised.
            failure = self._guard.password_check_failed(
                db, user, ip=ip, now=now, locked_until=locked_until
            )
        raise failure

    def sign_out_everywhere(self, user_id: uuid.UUID, *, ip: str | None = None) -> None:
        """End every session of the user, the caller's own included. Records a
        ``signed_out_everywhere`` security event from ``ip``."""
        with Session(self._engine) as db, db.begin():
            ended = db.execute(
                sa.delete(SessionRecord).where(SessionRecord.user_id == user_id)
            ).rowcount
            self._events.record(
                "signed_out_everywhere",
                actor_id=user_id,
                target_type="user",
                target_id=user_id,
                metadata={"sessions_ended": ended},
                ip=ip,
                db=db,
            )

    def sign_out(self, token: str) -> None:
        """End the session with this token (nothing happens if there is none)."""
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.delete(SessionRecord).where(SessionRecord.token_hash == _token_hash(token))
            )

    def _new_user(
        self, email: str, password: str, display_name: str, system_role: SystemRole
    ) -> UserRecord:
        return _new_user_record(email, password, display_name, system_role, now=self._clock())

    @staticmethod
    def _existing_user(db: Session, user_id: uuid.UUID) -> UserRecord:
        user = db.get(UserRecord, user_id, with_for_update=True)
        if user is None:
            raise ApiError(404, "not_found", "The user does not exist.")
        return user

    @staticmethod
    def _verify(password_hash: str, password: str) -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


RESET_LINK_LIFETIME = timedelta(minutes=30)


def _invalid_reset_token() -> ApiError:
    # One error for unknown, used and expired links, so none can be told apart.
    return ApiError(
        400,
        "invalid_reset_token",
        "This password reset link is invalid, used or expired. Ask for a new one.",
    )


class PasswordResets:
    """Forgotten passwords (spec §6.1): a single-use link, valid 30 minutes, emailed
    through the ``Mailer``. The link carries a random token; the ``password_resets``
    row stores only its SHA-256. Using it sets the new password and ends every session
    of the user.

    Requests are throttled so the endpoint cannot flood a mailbox or the database: at
    most one link per account per ``DAWAM_PASSWORD_RESET_COOLDOWN_MINUTES``, and at most
    ``DAWAM_PASSWORD_RESET_IP_MAX_REQUESTS`` links caused by one client address per
    ``DAWAM_PASSWORD_RESET_IP_WINDOW_MINUTES``. A throttled request sends nothing and
    stores nothing; it is logged once."""

    def __init__(
        self, engine: sa.Engine, settings: Settings, *, mailer: Mailer, clock: Clock
    ) -> None:
        self._engine = engine
        self._mailer = mailer
        self._clock = clock
        self._public_url = settings.public_url
        self._cooldown = timedelta(minutes=settings.password_reset_cooldown_minutes)
        self._ip_max = settings.password_reset_ip_max_requests
        self._ip_window = timedelta(minutes=settings.password_reset_ip_window_minutes)
        self._events = SecurityEventRecorder(engine, clock=clock)

    def request_reset(self, email: str, *, ip: str | None = None) -> None:
        """Email a reset link to the user with this email, if there is an active one, and record
        a ``password_reset_requested`` security event (from ``ip``). Says nothing either
        way, so callers can answer the same whether or not it exists."""
        now = self._clock()
        token = secrets.token_urlsafe(32)
        expires_at = now + RESET_LINK_LIFETIME
        with Session(self._engine) as db, db.begin():
            user = db.scalar(
                sa.select(UserRecord).where(UserRecord.email == normalize_email(email))
            )
            if user is None or not user.is_active:
                return  # a deactivated user could not sign in with a new password anyway
            user_id, address = user.id, user.email
            if self._throttled(db, user_id, ip, now):
                logger.warning(
                    "password reset request throttled", extra={"user_id": str(user_id), "ip": ip}
                )
                return
            db.add(
                PasswordResetRecord(
                    user_id=user_id,
                    token_hash=_token_hash(token),
                    created_at=now,
                    expires_at=expires_at,
                    requested_ip=ip[:IP_MAX_LENGTH] if ip else None,
                )
            )
            self._events.record(
                "password_reset_requested",
                actor_id=user_id,
                target_type="user",
                target_id=user_id,
                ip=ip,
                db=db,
            )
        self._send_link(address, token, expires_at)
        logger.info("password reset requested", extra={"user_id": str(user_id)})

    def force_reset(
        self, user_id: uuid.UUID, *, actor_id: uuid.UUID, ip: str | None = None
    ) -> Delivery:
        """An admin's answer to a suspected compromise (spec story 19): end every session
        of the user, make their password stop working and send them a reset link (kept
        for an admin to share when it cannot be emailed). Not throttled. Records a
        ``password_reset_forced`` security event naming ``actor_id``, from ``ip``.

        Raises ``ApiError`` 404 ``not_found``, 409 ``user_deactivated``."""
        now = self._clock()
        token = secrets.token_urlsafe(32)
        expires_at = now + RESET_LINK_LIFETIME
        with Session(self._engine) as db, db.begin():
            user = db.get(UserRecord, user_id, with_for_update=True)
            if user is None:
                raise ApiError(404, "not_found", "The user does not exist.")
            if not user.is_active:
                raise ApiError(
                    409,
                    "user_deactivated",
                    "The user is deactivated: reactivate them first.",
                )
            # Nobody knows this password: only the link lets the user back in.
            user.password_hash = _hasher.hash(secrets.token_urlsafe(32))
            ended = db.execute(
                sa.delete(SessionRecord).where(SessionRecord.user_id == user.id)
            ).rowcount
            db.add(
                PasswordResetRecord(
                    user_id=user.id,
                    token_hash=_token_hash(token),
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            self._events.record(
                "password_reset_forced",
                actor_id=actor_id,
                target_type="user",
                target_id=user.id,
                metadata={"email": user.email, "sessions_ended": ended},
                ip=ip,
                db=db,
            )
            address = user.email
        logger.info("password reset forced", extra={"user_id": str(user_id)})
        return self._send_link(address, token, expires_at, forced=True)

    def _send_link(
        self, address: str, token: str, expires_at: datetime, *, forced: bool = False
    ) -> Delivery:
        url = f"{self._public_url}/reset-password#token={token}"
        minutes = int(RESET_LINK_LIFETIME.total_seconds() // 60)
        if forced:
            why = (
                "A DAWAM administrator has reset the password of your account, so the old "
                "one no longer works and every session of yours has ended.\n\n"
            )
            ending = "The link works once. Ask an administrator for a new one if it expires.\n"
        else:
            why = "Someone (hopefully you) asked to reset the password of your DAWAM account.\n\n"
            ending = (
                "The link works once. If you did not ask for it, ignore this email: "
                "your password stays as it is.\n"
            )
        return self._mailer.send(
            EmailMessage(
                to=address,
                subject="Reset your DAWAM password",
                body=(
                    f"{why}"
                    f"To choose a new password, open this link within {minutes} minutes:\n\n"
                    f"{url}\n\n"
                    f"{ending}"
                ),
            ),
            link=OneTimeLink(url=url, purpose="password_reset", expires_at=expires_at),
        )

    def _throttled(self, db: Session, user_id: uuid.UUID, ip: str | None, now: datetime) -> bool:
        # Locks serialise concurrent requests for one account and for one address, so
        # neither limit can be raced past.
        db.execute(sa.select(UserRecord.id).where(UserRecord.id == user_id).with_for_update())
        recent_for_user = db.scalar(
            sa.select(
                sa.exists().where(
                    PasswordResetRecord.user_id == user_id,
                    PasswordResetRecord.created_at > now - self._cooldown,
                )
            )
        )
        if recent_for_user:
            return True
        if ip is None:
            return False
        ip = ip[:IP_MAX_LENGTH]
        db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(f"reset:{ip}"))))
        recent_for_ip = db.scalar(
            sa.select(sa.func.count()).where(
                PasswordResetRecord.requested_ip == ip,
                PasswordResetRecord.created_at > now - self._ip_window,
            )
        )
        return (recent_for_ip or 0) >= self._ip_max

    def reset_password(self, token: str, new_password: str, *, ip: str | None = None) -> None:
        """Set a new password with a reset link's token, end every session of the user,
        lift a sign-in lockout and clear ``must_change_password``. Every other unused link
        of the user stops working too. Records a ``password_reset`` security event (from ``ip``).

        Raises ``ApiError``: 422 ``invalid_password``, 400 ``invalid_reset_token``."""
        problem = password_problem(new_password)
        if problem:
            raise ApiError(422, "invalid_password", f"The password {problem}.")
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            reset = db.scalar(
                sa.select(PasswordResetRecord)
                .where(PasswordResetRecord.token_hash == _token_hash(token))
                .with_for_update()
            )
            if reset is None or reset.used_at is not None or now >= reset.expires_at:
                raise _invalid_reset_token()
            user = db.get(UserRecord, reset.user_id, with_for_update=True)
            if user is None:
                raise _invalid_reset_token()
            user.password_hash = _hasher.hash(new_password)
            # A password the user chose themselves: no temporary one left to replace.
            user.must_change_password = False
            # Whoever reads the user's email may choose their password, so a lock
            # against password guessing has nothing left to protect.
            user.failed_login_count = 0
            user.locked_until = None
            user.last_failed_login_at = None
            self._events.record(
                "password_reset",
                actor_id=user.id,
                target_type="user",
                target_id=user.id,
                ip=ip,
                db=db,
            )
            db.execute(
                sa.update(PasswordResetRecord)
                .where(
                    PasswordResetRecord.user_id == user.id,
                    PasswordResetRecord.used_at.is_(None),
                )
                .values(used_at=now)
            )
            db.execute(sa.delete(SessionRecord).where(SessionRecord.user_id == user.id))
            user_id, address = user.id, user.email
        # Every reset link of the user is dead now: an admin need not share any.
        self._mailer.withdraw_links(recipient=address, purpose="password_reset")
        logger.info("password reset", extra={"user_id": str(user_id)})
