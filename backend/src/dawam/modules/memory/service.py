"""Workspace memory (ADR 0010): short facts the team confirmed, for the assistant to recall.

A memory item is proposed as a ``memory_item`` Change Set item and exists only once a member
accepts it, so everything ``list`` and ``recall`` return is approved. Its text is plain, at
most ``TEXT_MAX_LENGTH`` characters and free of personal data: the PII validators (regex and,
when it is on, NER) are asked, and a hit refuses the item rather than masking it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import AppliedChange, ChangeSetDetail, ChangeSetService, ProposedItem
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .tables import TEXT_MAX_LENGTH, MemoryItemRecord

OBJECT_TYPE = "memory_item"
MAX_PROPOSED = 10
"""Items one proposal may hold."""
RECALL_LIMIT = 10
LIST_LIMIT = 500
_SCANNED = 2000
"""The newest items ``recall`` ranks."""
_WORD = re.compile(r"\w{2,}")


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


def checked_text(value: Any) -> str:
    """``value`` as a memory item's text, trimmed; 422 ``invalid_change_set`` when it is empty,
    too long or holds personal data (which is named by rule, never echoed)."""
    if not isinstance(value, str) or not value.strip():
        raise _invalid("A memory item is non-empty plain text.")
    text = value.strip()
    if len(text) > TEXT_MAX_LENGTH:
        raise _invalid(
            f"A memory item holds at most {TEXT_MAX_LENGTH} characters; this one has {len(text)}."
        )
    found: list[str] = []
    redact_text(text, found=found)
    if found:
        raise _invalid(
            "A memory item may not hold personal data; found "
            f"{', '.join(sorted(set(found)))}. Leave the personal values out."
        )
    return text


def _label(text: str) -> str:
    return text if len(text) <= 80 else text[:79] + "…"


class MemoryItemHandler:
    """The Change Set engine's handler for ``memory_item`` objects: an item only creates."""

    object_type = OBJECT_TYPE

    def required_action(self, operation: str) -> Action:
        if operation != "create":
            raise ValueError("Change Sets only create memory items.")
        return Action.REVIEW_CHANGE_SETS

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if set(payload) != {"text"}:
            raise _invalid("A memory_item item has exactly one field, `text`.")
        checked_text(payload["text"])

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = db.get(MemoryItemRecord, object_id, with_for_update=True)
        if record is None or record.workspace_id != workspace_id:
            return None
        return {f: record.text for f in fields if f == "text"}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        text = checked_text(payload.get("text"))  # again: the PII rules may have grown
        record = MemoryItemRecord(workspace_id=workspace_id, text=text, created_at=at)
        db.add(record)
        db.flush()
        return AppliedChange(
            entity_type=OBJECT_TYPE,
            entity_id=record.id,
            old=None,
            new={"text": text},
            label=_label(text),
        )


@dataclass(frozen=True)
class MemoryItem:
    id: uuid.UUID
    text: str
    created_at: datetime


def _view(record: MemoryItemRecord) -> MemoryItem:
    return MemoryItem(record.id, record.text, record.created_at)


class MemoryService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        change_sets: ChangeSetService | None = None,
    ) -> None:
        """``change_sets`` is needed only to ``propose``."""
        self._engine = engine
        self._workspaces = workspaces
        self._change_sets = change_sets

    def propose(
        self,
        user: User,
        workspace_id: uuid.UUID,
        texts: Sequence[str],
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> ChangeSetDetail:
        """One pending Change Set with a ``memory_item`` per text (owners and editors). Each
        proposal has a scope of its own, so it never supersedes another. 422
        ``invalid_change_set`` if any text is refused."""
        if self._change_sets is None:
            raise RuntimeError("MemoryService was built without a ChangeSetService")
        self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        if not texts or len(texts) > MAX_PROPOSED:
            raise _invalid(f"Propose 1 to {MAX_PROPOSED} memory items at a time.")
        items = []
        for position, raw in enumerate(texts):
            text = checked_text(raw)
            items.append(
                ProposedItem(
                    key=f"memory_{position}",
                    object_type=OBJECT_TYPE,
                    operation="create",
                    payload={"text": text},
                    label=_label(text),
                )
            )
        title = (
            f"Remember: {_label(items[0].payload['text'])}"
            if len(items) == 1
            else f"Remember {len(items)} facts"
        )
        return self._change_sets.propose(
            user,
            workspace_id,
            origin="ai",
            scope={"kind": "workspace_memory", "proposal": str(uuid.uuid4())},
            title=title,
            conversation_id=conversation_id,
            items=items,
        )

    def list(self, user: User, workspace_id: uuid.UUID) -> list[MemoryItem]:
        """The Workspace's memory items, newest first (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return [_view(r) for r in self._newest(db, workspace_id, LIST_LIMIT)]

    def recall(
        self, user: User, workspace_id: uuid.UUID, query: str, *, limit: int = RECALL_LIMIT
    ) -> list[MemoryItem]:
        """The items that best match ``query``: most query words found first, then newest.
        Items matching none are left out; a query without words returns the newest."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        words = list(dict.fromkeys(w.lower() for w in _WORD.findall(query)))
        with Session(self._engine) as db:
            records = self._newest(db, workspace_id, _SCANNED)
        if not words:
            return [_view(r) for r in records[:limit]]
        scored = [(sum(w in r.text.lower() for w in words), i, r) for i, r in enumerate(records)]
        ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        return [_view(r) for _, _, r in ranked[:limit]]

    def _newest(self, db: Session, workspace_id: uuid.UUID, limit: int) -> list[MemoryItemRecord]:
        return list(
            db.scalars(
                sa.select(MemoryItemRecord)
                .where(MemoryItemRecord.workspace_id == workspace_id)
                .order_by(MemoryItemRecord.created_at.desc(), MemoryItemRecord.id)
                .limit(limit)
            )
        )
