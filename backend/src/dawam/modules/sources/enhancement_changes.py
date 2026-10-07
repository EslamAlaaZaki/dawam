"""Source Schema enhancements as Change Set items (spec §6.10, story 147).

``SourceEnhancementHandler`` is the ``changesets`` engine's handler for ``source_table``
and ``source_column`` objects: an item updates the description, tags, sensitivity flag
and, for tables, the classification and SCD hint. The engine decides staleness (per
changed field), role, ordering and audit; this class reads and changes the objects.
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

from .enhancement_service import COLUMN_FIELDS, TABLE_FIELDS, _snapshot, set_fields
from .tables import SourceSystemRecord, SrcColumnRecord, SrcDbSchemaRecord, SrcTableRecord

TABLE = "source_table"
COLUMN = "source_column"


class SourceEnhancementHandler:
    """Serves one of the two object types; register one instance of each."""

    def __init__(self, object_type: str) -> None:
        if object_type not in (TABLE, COLUMN):
            raise ValueError(f"not a Source Schema object type: {object_type!r}")
        self.object_type = object_type
        self._fields = TABLE_FIELDS if object_type == TABLE else COLUMN_FIELDS

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError(
                "Source Schema objects come from extraction: Change Sets only update them."
            )
        return Action.EDIT_SOURCE_ENHANCEMENTS

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        unknown = [f for f in payload if f not in self._fields]
        if unknown or not payload:
            raise ApiError(
                422,
                "invalid_change_set",
                f"A {self.object_type} item changes some of {', '.join(self._fields)}"
                + (f", not {', '.join(unknown)}" if unknown else "")
                + ".",
            )
        record = self._record(db, workspace_id, object_id)
        if record is None:
            raise ApiError(422, "invalid_change_set", f"The {self.object_type} does not exist.")
        # Dry run on the loaded object: validation errors surface now, nothing is saved.
        with db.no_autoflush:
            set_fields(record, self.object_type, payload)
        db.expire(record)

    def in_scope(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        scope: Mapping[str, Any],
    ) -> bool:
        """The object belongs to the Source System the scope names (if it names one)."""
        wanted = scope.get("source_system_id")
        if wanted is None:
            return True
        query = sa.select(SrcDbSchemaRecord.source_system_id).join(
            SrcTableRecord, SrcTableRecord.db_schema_id == SrcDbSchemaRecord.id
        )
        if self.object_type == COLUMN:
            query = query.join(SrcColumnRecord, SrcColumnRecord.table_id == SrcTableRecord.id)
            query = query.where(SrcColumnRecord.id == object_id)
        else:
            query = query.where(SrcTableRecord.id == object_id)
        return str(db.scalar(query)) == str(wanted)

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:
            return None
        return _snapshot(record, tuple(fields))

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
            raise ApiError(409, "version_conflict", "The object no longer exists.")
        before = _snapshot(record, self._fields)
        set_fields(record, self.object_type, payload)
        after = _snapshot(record, self._fields)
        changed = [f for f in self._fields if before[f] != after[f]]
        if changed:
            record.version += 1
        db.flush()
        return AppliedChange(
            entity_type=self.object_type,
            entity_id=record.id,
            old={f: before[f] for f in changed},
            new={f: after[f] for f in changed},
        )

    def _record(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID | None,
        *,
        lock: bool = False,
    ) -> SrcTableRecord | SrcColumnRecord | None:
        if object_id is None:
            return None
        if self.object_type == TABLE:
            query = (
                sa.select(SrcTableRecord)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .join(
                    SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id
                )
                .where(SrcTableRecord.id == object_id)
            )
            of = SrcTableRecord
        else:
            query = (
                sa.select(SrcColumnRecord)
                .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .join(
                    SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id
                )
                .where(SrcColumnRecord.id == object_id)
            )
            of = SrcColumnRecord
        query = query.where(
            SourceSystemRecord.workspace_id == workspace_id, SourceSystemRecord.status == "present"
        )
        if lock:
            query = query.with_for_update(of=of)
        return db.scalars(query).first()
