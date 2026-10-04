"""Brute-force protection for sign-in (spec §6.1 rate limiting): account lockout.

After ``DAWAM_LOGIN_MAX_FAILURES`` consecutive failed sign-ins an account locks for
``DAWAM_LOGIN_LOCKOUT_MINUTES``; while locked, even the right password is refused.
A successful sign-in resets the count, and so does locking.

An email without an account locks in exactly the same way, so neither the lock nor
its timing reveals which emails have accounts. Its state is derived from its rows in
``login_failures`` instead of a ``users`` row: of its failures whose password was
checked, every ``max_failures``-th one locks it for the lockout period.

All state lives in PostgreSQL, so it holds across app replicas and restarts.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.config import Settings
from dawam.platform.errors import ApiError

from ..tables import IP_MAX_LENGTH, LoginFailureRecord, UserRecord
from .security_events import SecurityEventRecorder


def invalid_credentials() -> ApiError:
    # One error for an unknown email and a wrong password, so neither can be probed.
    return ApiError(401, "invalid_credentials", "The email or password is incorrect.")


def _retry_after(until: datetime, now: datetime) -> tuple[int, dict[str, str], str]:
    seconds = max(1, math.ceil((until - now).total_seconds()))
    minutes = math.ceil(seconds / 60)
    wait = f"{minutes} minute{'' if minutes == 1 else 's'}"
    return seconds, {"Retry-After": str(seconds)}, wait


def account_locked(until: datetime, now: datetime) -> ApiError:
    seconds, headers, wait = _retry_after(until, now)
    return ApiError(
        429,
        "account_locked",
        f"Too many failed sign-ins: this account is locked. Try again in {wait}.",
        {"retry_after_seconds": seconds},
        headers=headers,
    )


class LoginGuard:
    def __init__(self, settings: Settings, events: SecurityEventRecorder) -> None:
        self._max_failures = settings.login_max_failures
        self._lockout = timedelta(minutes=settings.login_lockout_minutes)
        self._events = events

    def locked_until(
        self, db: Session, user: UserRecord | None, email: str, now: datetime
    ) -> datetime | None:
        """When the lock on this account (or email without one) ends; None if unlocked."""
        if user is not None:
            until = user.locked_until
        else:
            count, last = db.execute(
                sa.select(sa.func.count(), sa.func.max(LoginFailureRecord.failed_at)).where(
                    LoginFailureRecord.email == email, LoginFailureRecord.password_checked
                )
            ).one()
            until = last + self._lockout if count and count % self._max_failures == 0 else None
        return until if until is not None and now < until else None

    def failed(
        self,
        db: Session,
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
        db.add(
            LoginFailureRecord(
                email=email,
                ip=ip[:IP_MAX_LENGTH] if ip else None,
                failed_at=now,
                password_checked=locked_until is None,
            )
        )
        target = {"target_type": "user", "target_id": user.id} if user else {}
        reason = "account_locked" if locked_until else "invalid_credentials"
        self._events.record(
            "login_failed", metadata={"email": email, "reason": reason}, ip=ip, db=db, **target
        )
        if locked_until is not None:
            return account_locked(locked_until, now)
        if not self._reaches_the_limit(db, user, email):
            return invalid_credentials()
        until = now + self._lockout
        if user is not None:
            user.failed_login_count = 0
            user.locked_until = until
        self._events.record(
            "account_locked",
            metadata={
                "email": email,
                "failed_attempts": self._max_failures,
                "locked_until": until.isoformat(),
            },
            ip=ip,
            db=db,
            **target,
        )
        return account_locked(until, now)

    def succeeded(self, user: UserRecord) -> None:
        user.failed_login_count = 0
        user.locked_until = None

    def _reaches_the_limit(self, db: Session, user: UserRecord | None, email: str) -> bool:
        if user is not None:
            user.failed_login_count += 1
            return user.failed_login_count >= self._max_failures
        db.flush()
        count = db.scalar(
            sa.select(sa.func.count()).where(
                LoginFailureRecord.email == email, LoginFailureRecord.password_checked
            )
        )
        return bool(count) and count % self._max_failures == 0
