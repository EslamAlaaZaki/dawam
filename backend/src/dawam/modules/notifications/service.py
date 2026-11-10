"""Notifications: one row per recipient, a paged list per user.

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
from dawam.platform.errors import ApiError
from dawam.platform.pagination import decode_cursor, encode_cursor

from .tables import MESSAGE_MAX_LENGTH, NotificationRecord

NotificationKind = Literal[
    "mention", "impact_alert", "sync_alert", "ownership", "job", "needs_owner", "regeneration"
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


@dataclass(frozen=True)
class NotificationPage:
    items: list[Notification]
    """One page of the user's notifications, read and unread, newest first."""
    unread_count: int
    """All the user's unread notifications, on every page."""
    next_cursor: str | None
    """Where the next, older page starts; ``None`` on the last page."""


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
        unless_unread: bool = False,
    ) -> int:
        """Give each user in ``recipient_ids`` (once) an unread notification; returns how
        many were created. Pass ``db`` to write them in that session's transaction, so
        they are kept only if the caller's change is; without it they are committed on
        their own. With ``unless_unread``, a user who still has an unread one of the same
        kind, Workspace and reference gets no other. ``ValueError`` for an unknown
        ``kind``."""
        if kind not in NOTIFICATION_KINDS:
            raise ValueError(f"unknown notification kind {kind!r}")
        recipients = list(dict.fromkeys(recipient_ids))
        if unless_unread and recipients:
            unread = sa.select(NotificationRecord.user_id).where(
                NotificationRecord.user_id.in_(recipients),
                NotificationRecord.read_at.is_(None),
                NotificationRecord.kind == kind,
                NotificationRecord.workspace_id.is_not_distinct_from(workspace_id),
                NotificationRecord.ref_type.is_not_distinct_from(ref_type),
                NotificationRecord.ref_id.is_not_distinct_from(ref_id),
            )
            if db is not None:
                taken = set(db.scalars(unread))
            else:
                with Session(self._engine) as own:
                    taken = set(own.scalars(unread))
            recipients = [r for r in recipients if r not in taken]
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
            for user_id in recipients
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

    def page(self, user: User, *, limit: int, cursor: str | None = None) -> NotificationPage:
        """``user``'s notifications, read and unread, newest first, ``limit`` at a time
        (keyset on ``(created_at desc, id)``). Raises ``ApiError`` 422 ``invalid_cursor``."""
        statement = (
            sa.select(NotificationRecord)
            .where(NotificationRecord.user_id == user.id)
            .order_by(NotificationRecord.created_at.desc(), NotificationRecord.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            created_at, record_id = decode_cursor(cursor, 2)
            try:
                after = (datetime.fromisoformat(created_at), uuid.UUID(record_id))
            except ValueError:
                raise ApiError(
                    422, "invalid_cursor", "The cursor is not valid; start from the first page."
                ) from None
            statement = statement.where(
                sa.or_(
                    NotificationRecord.created_at < after[0],
                    sa.and_(
                        NotificationRecord.created_at == after[0], NotificationRecord.id > after[1]
                    ),
                )
            )
        with Session(self._engine) as db:
            records = db.scalars(statement).all()
            count = db.scalar(
                sa.select(sa.func.count()).where(
                    NotificationRecord.user_id == user.id, NotificationRecord.read_at.is_(None)
                )
            )
        shown = records[:limit]
        next_cursor = (
            encode_cursor(shown[-1].created_at.isoformat(), str(shown[-1].id))
            if len(records) > limit
            else None
        )
        return NotificationPage(
            items=[_notification(r) for r in shown],
            unread_count=count or 0,
            next_cursor=next_cursor,
        )

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
