"""Relationship decisions as Change Set items (spec §6.8, story 70).

``SourceRelationshipHandler`` is the ``changesets`` engine's handler for
``source_relationship`` objects: an item accepts or rejects an inferred relationship and may
carry the AI's evidence for that decision (``ai_evidence``: its reason and the counts its
source queries returned, never a value), which is kept under ``evidence["ai"]``. The engine
decides staleness, role, ordering and audit; this class reads and changes the relationship.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from dawam.modules.activity import record_activity
from dawam.modules.changesets import AppliedChange
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .tables import (
    RelationshipRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

RELATIONSHIP = "source_relationship"
FIELDS = ("status", "ai_evidence")
DECISIONS = ("accepted", "rejected")


def _values(record: RelationshipRecord, fields: Sequence[str]) -> dict[str, Any]:
    values = {"status": record.status, "ai_evidence": (record.evidence or {}).get("ai")}
    return {f: values[f] for f in fields if f in values}


class SourceRelationshipHandler:
    object_type = RELATIONSHIP
    wants_actor = True

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("A relationship item accepts or rejects an inferred relationship.")
        return Action.EDIT_SOURCE_ENHANCEMENTS

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if payload.get("status") not in DECISIONS or any(f not in FIELDS for f in payload):
            raise ApiError(
                422,
                "invalid_change_set",
                "A source_relationship item sets `status` to accepted or rejected, with "
                "optional `ai_evidence`.",
            )
        if not isinstance(payload.get("ai_evidence", {}), dict):
            raise ApiError(422, "invalid_change_set", "`ai_evidence` is an object.")
        record = self._record(db, workspace_id, object_id)
        if record is None:
            raise ApiError(422, "invalid_change_set", "The relationship does not exist.")
        if record.status != "suggested":
            raise ApiError(
                422,
                "invalid_change_set",
                f"The relationship was already {record.status} by a member; only suggested "
                "ones are decided here.",
            )

    def in_scope(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        scope: Mapping[str, Any],
    ) -> bool:
        """The relationship's columns belong to the Source System the scope names."""
        wanted = scope.get("source_system_id")
        if wanted is None:
            return True
        found = db.scalar(
            self._query(workspace_id, object_id).with_only_columns(
                SrcDbSchemaRecord.source_system_id
            )
        )
        return str(found) == str(wanted)

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        return None if record is None else _values(record, fields)

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The relationship no longer exists.")
        before = _values(record, FIELDS)
        label = self.label(db, workspace_id, record.id)
        if "ai_evidence" in payload:
            # A new dict, so the JSON column sees the change.
            record.evidence = {**(record.evidence or {}), "ai": dict(payload["ai_evidence"])}
        if record.status != payload["status"]:
            record.status = payload["status"]
            record.decided_by = actor_id
            record.decided_at = at
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=actor_id,
                verb=f"relationship.{record.status}",
                object_type="relationship",
                object_id=record.id,
                object_label=label,
                details={"origin": record.origin, "confidence": record.confidence, "via": "ai"},
                at=at,
            )
        after = _values(record, FIELDS)
        changed = [f for f in FIELDS if before[f] != after[f]]
        if changed:
            record.version += 1
        db.flush()
        return AppliedChange(
            entity_type="relationship",
            entity_id=record.id,
            old={f: before[f] for f in changed},
            new={f: after[f] for f in changed},
            label=label,
        )

    def label(self, db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID) -> str:
        """``schema.table.column -> schema.table.column``, or ``""`` if it is not found."""
        to_column = aliased(SrcColumnRecord)
        to_table = aliased(SrcTableRecord)
        to_schema = aliased(SrcDbSchemaRecord)
        row = db.execute(
            self._query(workspace_id, object_id)
            .join(to_column, to_column.id == RelationshipRecord.to_column_id)
            .join(to_table, to_table.id == to_column.table_id)
            .join(to_schema, to_schema.id == to_table.db_schema_id)
            .with_only_columns(
                SrcDbSchemaRecord.name,
                SrcTableRecord.name,
                SrcColumnRecord.name,
                to_schema.name,
                to_table.name,
                to_column.name,
            )
        ).first()
        if row is None:
            return ""
        return f"{'.'.join(row[:3])} -> {'.'.join(row[3:])}"

    def _query(self, workspace_id: uuid.UUID, object_id: uuid.UUID) -> sa.Select:
        return (
            sa.select(RelationshipRecord)
            .join(SrcColumnRecord, SrcColumnRecord.id == RelationshipRecord.from_column_id)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
            .where(
                RelationshipRecord.id == object_id,
                SourceSystemRecord.workspace_id == workspace_id,
                SourceSystemRecord.status == "present",
            )
        )

    def _record(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID | None,
        *,
        lock: bool = False,
    ) -> RelationshipRecord | None:
        if object_id is None:
            return None
        query = self._query(workspace_id, object_id)
        if lock:
            query = query.with_for_update(of=RelationshipRecord)
        return db.scalars(query).first()
