from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.config import ConfigError, Settings
from dawam.platform.credentials import is_valid_email, normalize_email, password_problem
from dawam.platform.errors import ApiError

from .tables import IP_MAX_LENGTH, USER_AGENT_MAX_LENGTH, SessionRecord, UserRecord

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


def _invalid_credentials() -> ApiError:
    # One error for an unknown email and a wrong password, so neither can be probed.
    return ApiError(401, "invalid_credentials", "The email or password is incorrect.")


def _email_taken() -> ApiError:
    return ApiError(409, "email_taken", "A user with this email already exists.")


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    email: str
    display_name: str
    system_role: SystemRole
    last_login_at: datetime | None


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
    )


class AuthService:
    """Users, passwords and server-side sessions."""

    def __init__(self, engine: sa.Engine, settings: Settings, *, clock: Clock) -> None:
        self._engine = engine
        self._settings = settings
        self._clock = clock

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
        try:
            with Session(self._engine) as db, db.begin():
                db.add(record)
                db.flush()
                return _user(record)
        except IntegrityError as exc:
            # The unique constraint, not a pre-check, decides: two concurrent creates
            # of one email cannot both succeed.
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_users_email":
                raise _email_taken() from None
            raise

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
        set and no admin exists. Never changes an existing user: if a non-admin user
        already has that email, raises ``ConfigError`` so startup stops."""
        if self._settings.admin_email is None or self._settings.admin_password is None:
            return
        email = normalize_email(self._settings.admin_email)
        password = self._settings.admin_password.get_secret_value()
        with Session(self._engine) as db, db.begin():
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(_BOOTSTRAP_LOCK_KEY)))
            if db.scalar(sa.select(sa.exists().where(UserRecord.system_role == "admin"))):
                return
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
        recorded on the session."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            user = db.scalar(
                sa.select(UserRecord).where(UserRecord.email == normalize_email(email))
            )
            if user is None:
                self._verify(_DUMMY_HASH, password)
                raise _invalid_credentials()
            if not self._verify(user.password_hash, password):
                raise _invalid_credentials()
            if _hasher.check_needs_rehash(user.password_hash):
                user.password_hash = _hasher.hash(password)
            user.last_login_at = now
            if replacing:
                db.execute(
                    sa.delete(SessionRecord).where(
                        SessionRecord.token_hash == _token_hash(replacing)
                    )
                )
            token = secrets.token_urlsafe(32)
            db.add(
                SessionRecord(
                    token_hash=_token_hash(token),
                    user_id=user.id,
                    created_at=now,
                    last_seen_at=now,
                    ip=ip[:IP_MAX_LENGTH] if ip else None,
                    user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
                )
            )
            return SignedIn(token=token, user=_user(user))

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
                now >= session.created_at + self.absolute_timeout
                or now >= session.last_seen_at + self.idle_timeout
            ):
                db.delete(session)
                return None
            if now - session.last_seen_at >= _TOUCH_INTERVAL:
                session.last_seen_at = now
            return _user(user)

    def sign_out(self, token: str) -> None:
        """End the session with this token (nothing happens if there is none)."""
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.delete(SessionRecord).where(SessionRecord.token_hash == _token_hash(token))
            )

    def _new_user(
        self, email: str, password: str, display_name: str, system_role: SystemRole
    ) -> UserRecord:
        email = normalize_email(email)
        if not is_valid_email(email):
            raise ApiError(422, "invalid_email", "The email address is not valid.")
        problem = password_problem(password)
        if problem:
            raise ApiError(422, "invalid_password", f"The password {problem}.")
        return UserRecord(
            email=email,
            display_name=display_name.strip(),
            password_hash=_hasher.hash(password),
            system_role=system_role,
            created_at=self._clock(),
        )

    @staticmethod
    def _verify(password_hash: str, password: str) -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
