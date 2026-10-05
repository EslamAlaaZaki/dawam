"""Notifications: one row per recipient, an unread list per user.

Any module raises one through ``NotificationService.notify``; the kinds are the spec's
(§7 ``Notification``), so a later epic that adds its own (jobs, mentions, ...) uses its
kind without changing this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.platform.clock import Clock

from .tables import MESSAGE_MAX_LENGTH, NotificationRecord

NotificationKind = Literal[
    "mention", "impact_alert", "sync_alert", "ownership", "job", "needs_owner"
]
NOTIFICATION_KINDS: tuple[NotificationKind, ...] = get_args(NotificationKind)


@dataclass(frozen=True)
class Notification:
    id: uuid.UUID
    kind: NotificationKind
    message: str
    """What to show the user, already worded."""
    workspace_id: uuid.UUID | None
    ref_type: str | None
    ref_id: uuid.UUID | None
    """What it points at (e.g. a job), for the UI to link to."""
    created_at: datetime
    read_at: datetime | None


@dataclass(frozen=True)
class UnreadNotifications:
    items: list[Notification]
    """The newest unread ones, newest first."""
    unread_count: int
    """All the user's unread notifications, which may be more than ``items``."""


def _notification(record: NotificationRecord) -> Notification:
    if record.kind not in NOTIFICATION_KINDS:
        raise ValueError(f"notification {record.id} has an unknown kind {record.kind!r}")
    return Notification(
        id=record.id,
        kind=record.kind,  # type: ignore[arg-type]  # checked above
        message=record.message,
        workspace_id=record.workspace_id,
        ref_type=record.ref_type,
        ref_id=record.ref_id,
        created_at=record.created_at,
        read_at=record.read_at,
    )


class NotificationService:
    def __init__(self, engine: sa.Engine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def notify(
        self,
        recipient_ids: Iterable[uuid.UUID],
        *,
        kind: NotificationKind,
        message: str,
        workspace_id: uuid.UUID | None = None,
        ref_type: str | None = None,
        ref_id: uuid.UUID | None = None,
        db: Session | None = None,
    ) -> int:
        """Give each user in ``recipient_ids`` (once) an unread notification; returns how
        many were created. Pass ``db`` to write them in that session's transaction, so
        they are kept only if the caller's change is; without it they are committed on
        their own. ``ValueError`` for an unknown ``kind``."""
        if kind not in NOTIFICATION_KINDS:
            raise ValueError(f"unknown notification kind {kind!r}")
        now = self._clock()
        records = [
            NotificationRecord(
                id=uuid.uuid4(),
                user_id=user_id,
                workspace_id=workspace_id,
                kind=kind,
                message=message[:MESSAGE_MAX_LENGTH],
                ref_type=ref_type,
                ref_id=ref_id,
                created_at=now,
            )
            for user_id in dict.fromkeys(recipient_ids)
        ]
        if db is not None:
            db.add_all(records)
            db.flush()
        else:
            with Session(self._engine) as own, own.begin():
                own.add_all(records)
        return len(records)

    def unread(self, user: User, *, limit: int = 50) -> UnreadNotifications:
        """``user``'s unread notifications, newest first (at most ``limit``)."""
        unread = (NotificationRecord.user_id == user.id, NotificationRecord.read_at.is_(None))
        with Session(self._engine) as db:
            records = db.scalars(
                sa.select(NotificationRecord)
                .where(*unread)
                .order_by(NotificationRecord.created_at.desc(), NotificationRecord.id)
                .limit(limit)
            ).all()
            count = db.scalar(sa.select(sa.func.count()).where(*unread)) or 0
        return UnreadNotifications(items=[_notification(r) for r in records], unread_count=count)

    def mark_read(self, user: User, notification_id: uuid.UUID) -> None:
        """Mark one of ``user``'s notifications read. Idempotent, and a no-op for an id
        that is not theirs, so it never tells whether someone else's exists."""
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.update(NotificationRecord)
                .where(
                    NotificationRecord.id == notification_id,
                    NotificationRecord.user_id == user.id,
                    NotificationRecord.read_at.is_(None),
                )
                .values(read_at=self._clock())
            )

    def mark_all_read(self, user: User) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.update(NotificationRecord)
                .where(NotificationRecord.user_id == user.id, NotificationRecord.read_at.is_(None))
                .values(read_at=self._clock())
            )
