"""What admins do to other users' accounts (spec stories 14-19)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import decode_cursor, encode_cursor

from ..service import SystemRole, User, _email_must_be_free, _new_user_record, _user
from ..tables import SessionRecord, UserRecord
from .security_events import SecurityEventRecorder

_LIKE_SPECIAL = re.compile(r"[\\%_]")
"""What ``LIKE`` would read as a wildcard or escape; escaped so search text is literal."""

# Serialises changes that could take away the last active admin.
_ADMINS_LOCK_KEY = 0x0DA3A5

_ACTIVE_ADMIN = (UserRecord.system_role == "admin", UserRecord.is_active.is_(True))


@dataclass(frozen=True)
class UserPage:
    items: list[User]
    next_cursor: str | None
    """Pass it as ``cursor`` for the next page; None on the last."""


class UserAdministration:
    """Listing users and changing their accounts, for admins. Callers authorize first
    (``can`` with ``Action.MANAGE_USERS``); every change is recorded as a security
    event naming the admin as its actor."""

    def __init__(self, engine: sa.Engine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._events = SecurityEventRecorder(engine, clock=clock)

    def list_users(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        search: str | None = None,
        system_role: SystemRole | None = None,
        is_active: bool | None = None,
    ) -> UserPage:
        """Users by email, ``limit`` at a time: only those whose email or display name
        contains ``search`` (ignoring case), with ``system_role``, and active or not as
        ``is_active`` says, when given. Raises ``ApiError`` 422 ``invalid_cursor``."""
        statement = sa.select(UserRecord).order_by(UserRecord.email).limit(limit + 1)
        if search:
            pattern = "%" + _LIKE_SPECIAL.sub(lambda match: "\\" + match[0], search) + "%"
            statement = statement.where(
                sa.or_(
                    UserRecord.email.ilike(pattern, escape="\\"),
                    UserRecord.display_name.ilike(pattern, escape="\\"),
                )
            )
        if system_role is not None:
            statement = statement.where(UserRecord.system_role == system_role)
        if is_active is not None:
            statement = statement.where(UserRecord.is_active == is_active)
        if cursor is not None:
            (after,) = decode_cursor(cursor, 1)
            statement = statement.where(UserRecord.email > after)
        with Session(self._engine) as db:
            records = list(db.scalars(statement))
        page = records[:limit]
        next_cursor = encode_cursor(page[-1].email) if len(records) > limit else None
        return UserPage(items=[_user(record) for record in page], next_cursor=next_cursor)

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
        system_role: SystemRole,
        temporary_password: str,
        actor_id: uuid.UUID,
        ip: str | None = None,
    ) -> User:
        """Create a user with a temporary password (spec story 15a), as ``actor_id``
        from ``ip``: the user must change it before doing anything else
        (``must_change_password``). Records a ``user_created`` security event.

        Raises ``ApiError``: 422 ``invalid_email``, ``invalid_display_name`` or
        ``invalid_password``, 409 ``email_taken``."""
        record = _new_user_record(
            email,
            temporary_password,
            display_name,
            system_role,
            now=self._clock(),
            must_change_password=True,
        )
        with _email_must_be_free(), Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            self._events.record(
                "user_created",
                actor_id=actor_id,
                target_type="user",
                target_id=record.id,
                metadata={"email": record.email, "system_role": system_role},
                ip=ip,
                db=db,
            )
            return _user(record)

    def update_user(
        self,
        user_id: uuid.UUID,
        *,
        system_role: SystemRole | None = None,
        is_active: bool | None = None,
        actor_id: uuid.UUID,
        ip: str | None = None,
    ) -> User:
        """Change a user's system role, or deactivate or reactivate them, as
        ``actor_id`` from ``ip``. Deactivating ends every session of theirs at once;
        nothing they did is removed. Each change is recorded (``user_role_changed``,
        ``user_deactivated``, ``user_reactivated``); asking for what is already so
        changes and records nothing.

        Raises ``ApiError`` 404 ``not_found``, or 409 ``last_admin`` if the change would
        leave no active admin (then nothing changes)."""
        with Session(self._engine) as db, db.begin():
            # One change of admins at a time, so two admins demoting or deactivating
            # each other at once cannot both pass the last-admin check.
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(_ADMINS_LOCK_KEY)))
            user = db.get(UserRecord, user_id, with_for_update=True)
            if user is None:
                raise ApiError(404, "not_found", "The user does not exist.")
            was_active_admin = user.is_active and user.system_role == "admin"
            role_changed = system_role is not None and system_role != user.system_role
            activity_changed = is_active is not None and is_active != user.is_active
            if role_changed:
                self._events.record(
                    "user_role_changed",
                    actor_id=actor_id,
                    target_type="user",
                    target_id=user.id,
                    metadata={"email": user.email, "from": user.system_role, "to": system_role},
                    ip=ip,
                    db=db,
                )
                user.system_role = system_role  # type: ignore[assignment]  # not None here
            if activity_changed:
                user.is_active = bool(is_active)
                metadata: dict[str, object] = {"email": user.email}
                if not is_active:
                    metadata["sessions_ended"] = db.execute(
                        sa.delete(SessionRecord).where(SessionRecord.user_id == user.id)
                    ).rowcount
                self._events.record(
                    "user_reactivated" if is_active else "user_deactivated",
                    actor_id=actor_id,
                    target_type="user",
                    target_id=user.id,
                    metadata=metadata,
                    ip=ip,
                    db=db,
                )
            is_active_admin = user.is_active and user.system_role == "admin"
            if was_active_admin and not is_active_admin:
                db.flush()
                if not db.scalar(sa.select(sa.exists().where(*_ACTIVE_ADMIN))):
                    # Raised inside the transaction: everything above is rolled back.
                    raise ApiError(
                        409,
                        "last_admin",
                        "This is the last active admin; promote another user first.",
                    )
            return _user(user)
