"""The audit trail (spec §6.10): who changed what, when, how, and the old and new values.

Other modules write entries through ``record_audit``, in the transaction that makes
the change. They never touch the table.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import (
    ENTITY_ID_MAX_LENGTH,
    ENTITY_TYPE_MAX_LENGTH,
    VIA_VALUES,
    AuditEntryRecord,
)


@dataclass(frozen=True)
class AuditEntry:
    id: uuid.UUID
    workspace_id: uuid.UUID
    actor_id: uuid.UUID | None
    via: str
    entity_type: str
    entity_id: str
    old: dict[str, Any] | None
    """The values before the change (only the fields that changed); ``None`` for a create."""
    new: dict[str, Any] | None
    """The values after the change; ``None`` for a delete."""
    created_at: datetime


def record_audit(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str | uuid.UUID,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    at: datetime,
    via: str = "user",
    change_set_id: uuid.UUID | None = None,
) -> None:
    """Add an audit entry in ``db``'s transaction. ``old`` / ``new`` are JSON-serializable
    and never hold secrets."""
    if via not in VIA_VALUES:
        raise ValueError(f"unknown audit channel {via!r}")
    entity_id_text = str(entity_id)
    if len(entity_type) > ENTITY_TYPE_MAX_LENGTH or len(entity_id_text) > ENTITY_ID_MAX_LENGTH:
        raise ValueError("audit entity type or id is too long")
    db.add(
        AuditEntryRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            via=via,
            change_set_id=change_set_id,
            entity_type=entity_type,
            entity_id=entity_id_text,
            old=dict(old) if old is not None else None,
            new=dict(new) if new is not None else None,
            created_at=at,
        )
    )


class AuditService:
    """Reads the audit trail. Does not authorize: the caller checks the user may view
    the Workspace first."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def list(
        self, workspace_id: uuid.UUID, *, entity_type: str, entity_id: str | uuid.UUID
    ) -> list[AuditEntry]:
        """The entries of one entity, oldest first."""
        query = (
            sa.select(AuditEntryRecord)
            .where(
                AuditEntryRecord.workspace_id == workspace_id,
                AuditEntryRecord.entity_type == entity_type,
                AuditEntryRecord.entity_id == str(entity_id),
            )
            .order_by(AuditEntryRecord.created_at, AuditEntryRecord.id)
        )
        with Session(self._engine) as db:
            return [
                AuditEntry(
                    id=r.id,
                    workspace_id=r.workspace_id,
                    actor_id=r.actor_id,
                    via=r.via,
                    entity_type=r.entity_type,
                    entity_id=r.entity_id,
                    old=r.old,
                    new=r.new,
                    created_at=r.created_at,
                )
                for r in db.scalars(query)
            ]
