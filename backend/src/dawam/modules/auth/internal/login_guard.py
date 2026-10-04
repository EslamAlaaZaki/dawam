"""Brute-force protection for sign-in (spec §6.1 rate limiting), per account and
per client address. All its state lives in PostgreSQL (``login_failures`` and the
``users`` lock columns), so it holds across app replicas and restarts and needs no
extra infrastructure.

**Per account (lockout).** After ``DAWAM_LOGIN_MAX_FAILURES`` consecutive failed
sign-ins an account locks for ``DAWAM_LOGIN_LOCKOUT_MINUTES``; while locked, even the
right password is refused. A successful sign-in resets the count, and so does locking.
Failures more than a day apart do not add up: a streak is forgotten a day after its
last failure. An email without an account locks in exactly the same way, so neither
the lock nor its timing reveals which emails have accounts; its state is a
``login_lockouts`` row (keyed by the email's hash) instead of a ``users`` row. Either
row is locked for the whole attempt, so concurrent attempts are counted one at a time.

**Per address (throttling).** An address with ``DAWAM_LOGIN_IP_MAX_FAILURES`` failed
sign-ins in the last ``DAWAM_LOGIN_IP_WINDOW_MINUTES`` must wait until the oldest of
them leaves the window. Its attempts are refused before any account is looked at, so
they are not recorded, count towards nothing and lock no account: the limit slows
that address alone, and caps how many accounts it can lock per window.

**Sign-up (per address).** Every self-registration attempt from an address is
counted (``registration_attempts``, a separate counter): one with
``DAWAM_REGISTER_IP_MAX_ATTEMPTS`` attempts in the last
``DAWAM_REGISTER_IP_WINDOW_MINUTES`` must wait, the same way, so sign-up cannot be used
to create accounts in bulk, probe emails or burn CPU on password hashing.

**The password change** checks the current password, so guessing it there counts
against the same per-account state (``password_check_failed``): it locks the account
like failed sign-ins do, and a locked account cannot change its password either.

After each failure, ``prune`` deletes what no longer matters: failures older than the
address window, and ``login_lockouts`` rows that are no different from no row.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dawam.platform.config import Settings
from dawam.platform.errors import ApiError

from ..tables import (
    IP_MAX_LENGTH,
    LoginFailureRecord,
    LoginLockoutRecord,
    RegistrationAttemptRecord,
    UserRecord,
)
from .credentials import is_valid_email
from .security_events import SecurityEventRecorder

FAILURE_STREAK_TTL = timedelta(hours=24)
"""A failure this long after the previous one starts a new streak."""


def _email_metadata(email: str) -> dict[str, object]:
    """What an event records of the typed email: the address only if it is one, since
    people paste passwords into the email field by mistake."""
    if is_valid_email(email):
        return {"email": email}
    return {"email": None, "email_invalid": True}


def wrong_password() -> ApiError:
    return ApiError(400, "wrong_password", "The current password is incorrect.")


def invalid_credentials() -> ApiError:
    # One error for an unknown email and a wrong password, so neither can be probed.
    return ApiError(401, "invalid_credentials", "The email or password is incorrect.")


def _must_wait(code: str, why: str, until: datetime, now: datetime) -> ApiError:
    seconds = max(1, math.ceil((until - now).total_seconds()))
    minutes = math.ceil(seconds / 60)
    wait = f"{minutes} minute{'' if minutes == 1 else 's'}"
    return ApiError(
        429,
        code,
        f"{why} Try again in {wait}.",
        {"retry_after_seconds": seconds},
        {"Retry-After": str(seconds)},
    )


def account_locked(until: datetime, now: datetime) -> ApiError:
    return _must_wait(
        "account_locked", "Too many failed sign-ins: this account is locked.", until, now
    )


def too_many_attempts(
    until: datetime, now: datetime, why: str = "Too many failed sign-ins from your network."
) -> ApiError:
    return _must_wait("too_many_attempts", why, until, now)


def too_many_sign_ups(until: datetime, now: datetime) -> ApiError:
    return too_many_attempts(until, now, "Too many sign-ups from your network.")


def _window_ends(
    db: Session,
    ip_column: sa.ColumnElement[str],
    at_column: sa.ColumnElement[datetime],
    ip: str | None,
    now: datetime,
    max_count: int,
    window: timedelta,
) -> datetime | None:
    """When an address with ``max_count`` rows in the last ``window`` may go on; None if
    it may now."""
    if not ip:
        return None
    # The row whose leaving the window brings the address back under the limit.
    oldest_that_counts = db.scalar(
        sa.select(at_column)
        .where(ip_column == ip[:IP_MAX_LENGTH], at_column > now - window)
        .order_by(at_column.desc())
        .offset(max_count - 1)
        .limit(1)
    )
    return oldest_that_counts + window if oldest_that_counts else None


class LockoutState(Protocol):
    """What a ``users`` row and a ``login_lockouts`` row both hold."""

    failed_login_count: int
    locked_until: datetime | None
    last_failed_login_at: datetime | None


def _email_key(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


class LoginGuard:
    def __init__(self, settings: Settings, events: SecurityEventRecorder) -> None:
        self._max_failures = settings.login_max_failures
        self._lockout = timedelta(minutes=settings.login_lockout_minutes)
        self._ip_max_failures = settings.login_ip_max_failures
        self._ip_window = timedelta(minutes=settings.login_ip_window_minutes)
        self._register_max = settings.register_ip_max_attempts
        self._register_window = timedelta(minutes=settings.register_ip_window_minutes)
        self._events = events

    def throttled_until(self, db: Session, ip: str | None, now: datetime) -> datetime | None:
        """When this address may try again; None if it may now."""
        return _window_ends(
            db,
            LoginFailureRecord.ip,
            LoginFailureRecord.failed_at,
            ip,
            now,
            self._ip_max_failures,
            self._ip_window,
        )

    def sign_up_throttled_until(
        self, db: Session, ip: str | None, now: datetime
    ) -> datetime | None:
        """When this address may sign up again; None if it may now."""
        return _window_ends(
            db,
            RegistrationAttemptRecord.ip,
            RegistrationAttemptRecord.attempted_at,
            ip,
            now,
            self._register_max,
            self._register_window,
        )

    def sign_up_attempted(self, db: Session, ip: str | None, now: datetime) -> None:
        """Count a sign-up attempt from ``ip``, and forget those outside the window."""
        if ip:
            db.add(RegistrationAttemptRecord(ip=ip[:IP_MAX_LENGTH], attempted_at=now))
        old = (
            sa.select(RegistrationAttemptRecord.id)
            .where(RegistrationAttemptRecord.attempted_at <= now - self._register_window)
            .with_for_update(skip_locked=True)
        )
        db.execute(
            sa.delete(RegistrationAttemptRecord).where(RegistrationAttemptRecord.id.in_(old))
        )

    def state_of(
        self, db: Session, user: UserRecord | None, email: str, now: datetime
    ) -> LockoutState:
        """The lockout state of ``user`` (locked ``FOR UPDATE`` by the caller), or of
        ``email`` if it has no account: its ``login_lockouts`` row, created if missing
        and locked for the rest of the transaction."""
        if user is not None:
            return user
        # One statement that both creates a missing row and locks an existing one (the
        # no-op update), so a concurrent prune can never delete it in between.
        insert = pg_insert(LoginLockoutRecord).values(
            email_key=_email_key(email), failed_login_count=0, last_failed_login_at=now
        )
        upsert = (
            insert.on_conflict_do_update(
                index_elements=[LoginLockoutRecord.email_key],
                set_={"email_key": insert.excluded.email_key},
            )
            .returning(LoginLockoutRecord)
            .execution_options(populate_existing=True)
        )
        return db.scalars(sa.select(LoginLockoutRecord).from_statement(upsert)).one()

    @staticmethod
    def locked_until(state: LockoutState, now: datetime) -> datetime | None:
        """When the lock ends; None if not locked."""
        until = state.locked_until
        return until if until is not None and now < until else None

    def failed(
        self,
        db: Session,
        state: LockoutState,
        user: UserRecord | None,
        email: str,
        *,
        ip: str | None,
        now: datetime,
        locked_until: datetime | None,
    ) -> ApiError:
        """Record a failed sign-in and return the error to answer it with.

        ``locked_until`` is the lock the attempt met (it was refused unchecked), or
        None if its password was checked and wrong; such a failure counts and may
        lock the account."""
        if ip:
            db.add(LoginFailureRecord(ip=ip[:IP_MAX_LENGTH], failed_at=now))
        target = {"target_type": "user", "target_id": user.id} if user else {}
        reason = "account_locked" if locked_until else "invalid_credentials"
        self._events.record(
            "login_failed",
            metadata={**_email_metadata(email), "reason": reason},
            ip=ip,
            db=db,
            **target,
        )
        if locked_until is not None:
            return account_locked(locked_until, now)
        until = self._count_failure(db, state, _email_metadata(email), target, ip, now)
        return account_locked(until, now) if until else invalid_credentials()

    def password_check_failed(
        self,
        db: Session,
        user: UserRecord,
        *,
        ip: str | None,
        now: datetime,
        locked_until: datetime | None,
    ) -> ApiError:
        """Record a refused password change of ``user`` (locked ``FOR UPDATE`` by the
        caller) and return the error to answer it with: its current password was wrong,
        which counts like a failed sign-in and may lock the account, or the account was
        already locked until ``locked_until`` (the password was not checked)."""
        target = {"target_type": "user", "target_id": user.id}
        reason = "account_locked" if locked_until else "wrong_password"
        self._events.record(
            "password_change_failed",
            actor_id=user.id,
            metadata={"reason": reason},
            ip=ip,
            db=db,
            **target,
        )
        if locked_until is not None:
            return account_locked(locked_until, now)
        until = self._count_failure(db, user, {"email": user.email}, target, ip, now)
        return account_locked(until, now) if until else wrong_password()

    def _count_failure(
        self,
        db: Session,
        state: LockoutState,
        email_metadata: dict[str, object],
        target: dict[str, object],
        ip: str | None,
        now: datetime,
    ) -> datetime | None:
        """Add a failure to the streak; lock and return the lock's end once it is long
        enough, else None."""
        last = state.last_failed_login_at
        if last is None or now - last >= FAILURE_STREAK_TTL:
            state.failed_login_count = 0
        state.failed_login_count += 1
        state.last_failed_login_at = now
        if state.failed_login_count < self._max_failures:
            return None
        until = now + self._lockout
        state.failed_login_count = 0
        state.locked_until = until
        self._events.record(
            "account_locked",
            metadata={
                **email_metadata,
                "failed_attempts": self._max_failures,
                "locked_until": until.isoformat(),
            },
            ip=ip,
            db=db,
            **target,
        )
        return until

    def prune(self, db: Session, now: datetime) -> None:
        """Delete failures older than the address window, and lockout rows of unknown
        emails that are not locked and whose last failure is forgotten. Rows another
        attempt holds are skipped (the next prune gets them), so this never waits."""
        old_failures = (
            sa.select(LoginFailureRecord.id)
            .where(LoginFailureRecord.failed_at <= now - self._ip_window)
            .with_for_update(skip_locked=True)
        )
        db.execute(sa.delete(LoginFailureRecord).where(LoginFailureRecord.id.in_(old_failures)))
        forgotten = (
            sa.select(LoginLockoutRecord.email_key)
            .where(
                LoginLockoutRecord.last_failed_login_at <= now - FAILURE_STREAK_TTL,
                sa.or_(
                    LoginLockoutRecord.locked_until.is_(None),
                    LoginLockoutRecord.locked_until <= now,
                ),
            )
            .with_for_update(skip_locked=True)
        )
        db.execute(sa.delete(LoginLockoutRecord).where(LoginLockoutRecord.email_key.in_(forgotten)))
