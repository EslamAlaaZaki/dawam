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

from ..tables import IP_MAX_LENGTH, LoginFailureRecord, LoginLockoutRecord, UserRecord
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


def too_many_attempts(until: datetime, now: datetime) -> ApiError:
    return _must_wait(
        "too_many_attempts", "Too many failed sign-ins from your network.", until, now
    )


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
        self._events = events

    def throttled_until(self, db: Session, ip: str | None, now: datetime) -> datetime | None:
        """When this address may try again; None if it may now."""
        if not ip:
            return None
        # The failure whose leaving the window brings the address back under the limit.
        oldest_that_counts = db.scalar(
            sa.select(LoginFailureRecord.failed_at)
            .where(
                LoginFailureRecord.ip == ip[:IP_MAX_LENGTH],
                LoginFailureRecord.failed_at > now - self._ip_window,
            )
            .order_by(LoginFailureRecord.failed_at.desc())
            .offset(self._ip_max_failures - 1)
            .limit(1)
        )
        return oldest_that_counts + self._ip_window if oldest_that_counts else None

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
        last = state.last_failed_login_at
        if last is None or now - last >= FAILURE_STREAK_TTL:
            state.failed_login_count = 0
        state.failed_login_count += 1
        state.last_failed_login_at = now
        if state.failed_login_count < self._max_failures:
            return invalid_credentials()
        until = now + self._lockout
        state.failed_login_count = 0
        state.locked_until = until
        self._events.record(
            "account_locked",
            metadata={
                **_email_metadata(email),
                "failed_attempts": self._max_failures,
                "locked_until": until.isoformat(),
            },
            ip=ip,
            db=db,
            **target,
        )
        return account_locked(until, now)

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
