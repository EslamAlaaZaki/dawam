from __future__ import annotations

import functools
import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.errors import ApiError

from .tables import SessionRecord, UserRecord

logger = logging.getLogger(__name__)

SystemRole = Literal["admin", "user"]

MIN_PASSWORD_LENGTH = 10

# A session's last_seen_at is written at most this often, not on every request.
_TOUCH_INTERVAL = timedelta(minutes=1)

# Serialises bootstrap-admin creation when several app replicas start at once.
_BOOTSTRAP_LOCK_KEY = 0x0DA3A4

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# argon2-cffi's defaults: argon2id with the RFC 9106 low-memory parameters.
_hasher = PasswordHasher()


@functools.cache
def _dummy_hash() -> str:
    """A hash to verify against when the email is unknown, so both cases take as long."""
    return _hasher.hash(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _invalid_credentials() -> ApiError:
    # One error for an unknown email and a wrong password, so neither can be probed.
    return ApiError(401, "invalid_credentials", "The email or password is incorrect.")


@dataclass(frozen=True)
class User:
    id: uuid.UUID
    email: str
    display_name: str
    system_role: SystemRole


@dataclass(frozen=True)
class SignedIn:
    """A new session: ``token`` goes into the session cookie and nowhere else."""

    token: str
    user: User


def _user(record: UserRecord) -> User:
    role: SystemRole = "admin" if record.system_role == "admin" else "user"
    return User(
        id=record.id, email=record.email, display_name=record.display_name, system_role=role
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
        """Create a user. Raises ``ApiError`` (422/409) for an invalid or taken email or a
        password that is too short."""
        with Session(self._engine) as db, db.begin():
            if db.scalar(
                sa.select(UserRecord.id).where(UserRecord.email == normalize_email(email))
            ):
                raise ApiError(409, "email_taken", "A user with this email already exists.")
            record = self._new_user(email, password, display_name, system_role)
            db.add(record)
            db.flush()
            return _user(record)

    def ensure_bootstrap_admin(self) -> None:
        """Create the admin from ``DAWAM_ADMIN_EMAIL``/``DAWAM_ADMIN_PASSWORD`` if they are
        set and no admin exists. Never changes an existing user."""
        email, password = self._settings.admin_email, self._settings.admin_password
        if email is None or password is None:
            return
        with Session(self._engine) as db, db.begin():
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(_BOOTSTRAP_LOCK_KEY)))
            if db.scalar(sa.select(sa.exists().where(UserRecord.system_role == "admin"))):
                return
            if db.scalar(sa.select(sa.exists().where(UserRecord.email == normalize_email(email)))):
                logger.warning(
                    "bootstrap admin not created: a non-admin user already has its email",
                    extra={"email": normalize_email(email)},
                )
                return
            db.add(self._new_user(email, password.get_secret_value(), "Administrator", "admin"))
        logger.info("bootstrap admin created", extra={"email": normalize_email(email)})

    def sign_in(self, email: str, password: str, *, replacing: str | None = None) -> SignedIn:
        """Check the credentials and start a session. ``replacing`` is the token of the
        browser's current session, if any, which is ended."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            user = db.scalar(
                sa.select(UserRecord).where(UserRecord.email == normalize_email(email))
            )
            if user is None:
                self._verify(_dummy_hash(), password)
                raise _invalid_credentials()
            if not self._verify(user.password_hash, password):
                raise _invalid_credentials()
            if _hasher.check_needs_rehash(user.password_hash):
                user.password_hash = _hasher.hash(password)
            if replacing:
                db.execute(
                    sa.delete(SessionRecord).where(
                        SessionRecord.token_hash == _token_hash(replacing)
                    )
                )
            token = secrets.token_urlsafe(32)
            db.add(
                SessionRecord(
                    token_hash=_token_hash(token), user_id=user.id, created_at=now, last_seen_at=now
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
        if not _EMAIL.match(email):
            raise ApiError(422, "invalid_email", "The email address is not valid.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ApiError(
                422,
                "password_too_short",
                f"The password must be at least {MIN_PASSWORD_LENGTH} characters long.",
                {"min_length": MIN_PASSWORD_LENGTH},
            )
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
