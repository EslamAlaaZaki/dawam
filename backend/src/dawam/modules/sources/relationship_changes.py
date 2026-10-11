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
from sqlalchemy.orm import Session

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
        if self._record(db, workspace_id, object_id) is None:
            raise ApiError(422, "invalid_change_set", "The relationship does not exist.")

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
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The relationship no longer exists.")
        before = _values(record, FIELDS)
        if "ai_evidence" in payload:
            # A new dict, so the JSON column sees the change.
            record.evidence = {**(record.evidence or {}), "ai": dict(payload["ai_evidence"])}
        if record.status != payload["status"]:
            record.status = payload["status"]
            record.decided_at = at  # the actor is on the audit entry the engine writes
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
        )

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
