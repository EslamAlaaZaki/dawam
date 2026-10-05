"""The Workspace activity feed (spec story 38): who changed what, and when.

Other modules record events through ``record_activity``, in the transaction that makes
the change, so an event exists exactly when its change does. They never touch the
table. Reading the feed is ``ActivityService.list``; the caller authorizes first
(any member of the Workspace may read it).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import AuthService, User
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .tables import (
    OBJECT_ID_MAX_LENGTH,
    OBJECT_LABEL_MAX_LENGTH,
    OBJECT_TYPE_MAX_LENGTH,
    VERB_MAX_LENGTH,
    ActivityEventRecord,
)

USER_OBJECT = "user"
"""An ``object_type`` whose ``object_id`` is a user's id; the feed shows that user's
current display name rather than a stored label."""


@dataclass(frozen=True)
class ActivityActor:
    user_id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class ActivityItem:
    id: uuid.UUID
    actor: ActivityActor | None
    """``None`` when the actor no longer exists."""
    verb: str
    """Dotted, past tense: ``workspace.updated``, ``member.role_changed``, ..."""
    object_type: str
    object_id: str | None
    object_label: str
    details: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True)
class ActivityPage:
    items: list[ActivityItem]
    """Newest first."""
    next_cursor: str | None


def record_activity(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    verb: str,
    object_type: str,
    object_id: str | uuid.UUID | None = None,
    object_label: str = "",
    details: Mapping[str, Any] | None = None,
    at: datetime,
) -> None:
    """Add an activity event to the Workspace's feed, in ``db``'s transaction (it is
    committed or rolled back with the caller's change).

    ``verb`` is ``<thing>.<past-tense action>`` (e.g. ``workspace.updated``);
    ``object_type`` / ``object_id`` / ``object_label`` say what it was done to, the
    label being its name at that moment (for ``object_type="user"`` the feed shows the
    user's current name instead). ``details`` is optional JSON-serializable context
    (e.g. ``{"role": "editor"}``); ``at`` is when it happened (the caller's clock).
    """
    if len(verb) > VERB_MAX_LENGTH or len(object_type) > OBJECT_TYPE_MAX_LENGTH:
        raise ValueError("activity verb or object type is too long")
    object_id_text = None if object_id is None else str(object_id)
    if object_id_text is not None and len(object_id_text) > OBJECT_ID_MAX_LENGTH:
        raise ValueError("activity object id is too long")
    db.add(
        ActivityEventRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            verb=verb,
            object_type=object_type,
            object_id=object_id_text,
            object_label=object_label[:OBJECT_LABEL_MAX_LENGTH],
            details=dict(details) if details is not None else None,
            created_at=at,
        )
    )


class ActivityService:
    """Reads the activity feed. Does not authorize: the caller checks the user may
    view the Workspace first (``WorkspaceService.authorize``)."""

    def __init__(self, engine: sa.Engine, *, auth: AuthService) -> None:
        self._engine = engine
        self._auth = auth

    def list(
        self,
        workspace_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> ActivityPage:
        """The Workspace's events, newest first. Raises ``ApiError`` 422
        ``invalid_cursor`` for a cursor this method did not return."""
        query = (
            sa.select(ActivityEventRecord)
            .where(ActivityEventRecord.workspace_id == workspace_id)
            .order_by(ActivityEventRecord.created_at.desc(), ActivityEventRecord.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            after_at, after_id = decode_cursor(cursor, 2)
            try:
                at = datetime.fromisoformat(after_at)
                after_uuid = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.") from None
            if at.tzinfo is None:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.")
            query = query.where(
                sa.tuple_(ActivityEventRecord.created_at, ActivityEventRecord.id)
                < sa.tuple_(
                    sa.literal(at, sa.DateTime(timezone=True)),
                    sa.literal(after_uuid, sa.Uuid),
                )
            )
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        page = records[:limit]
        user_ids = {r.actor_id for r in page if r.actor_id is not None}
        user_ids |= {
            uuid.UUID(r.object_id)
            for r in page
            if r.object_type == USER_OBJECT and _is_uuid(r.object_id)
        }
        users = self._auth.users_by_id(user_ids)
        items = [_item(record, users) for record in page]
        next_cursor = None
        if len(records) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        return ActivityPage(items=items, next_cursor=next_cursor)


def _is_uuid(value: str | None) -> bool:
    try:
        uuid.UUID(value or "")
    except ValueError:
        return False
    return True


def _item(record: ActivityEventRecord, users: Mapping[uuid.UUID, User]) -> ActivityItem:
    actor = users.get(record.actor_id) if record.actor_id is not None else None
    label = record.object_label
    if record.object_type == USER_OBJECT and _is_uuid(record.object_id):
        target = users.get(uuid.UUID(record.object_id or ""))
        if target is not None:
            label = target.display_name
    return ActivityItem(
        id=record.id,
        actor=ActivityActor(user_id=actor.id, display_name=actor.display_name) if actor else None,
        verb=record.verb,
        object_type=record.object_type,
        object_id=record.object_id,
        object_label=label,
        details=record.details,
        created_at=record.created_at,
    )
