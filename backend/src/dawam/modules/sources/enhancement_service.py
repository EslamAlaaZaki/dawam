"""Source enhancements: business descriptions, tags, a sensitivity flag and, for tables,
a classification and an SCD hint (spec stories 61, 62).

They attach to the Source Object, so they survive Snapshots. Owners and editors edit;
every change is audited (``via=user``) and recorded in the activity feed, in the
transaction that makes it. Reading them is part of the Source Schema.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .tables import (
    CLASSIFICATIONS,
    DESCRIPTION_MAX_LENGTH,
    MAX_TAGS,
    SCD_HINT_MAX_LENGTH,
    TAG_MAX_LENGTH,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

TABLE_FIELDS = ("description", "tags", "is_sensitive", "classification", "scd_hint")
COLUMN_FIELDS = ("description", "tags", "is_sensitive")
"""The enhancements of a table and of a column: what the audit trail records."""


@dataclass(frozen=True)
class TableEnhancements:
    id: uuid.UUID
    description: str | None
    tags: list[str]
    is_sensitive: bool
    classification: str | None
    scd_hint: str | None
    version: int


@dataclass(frozen=True)
class ColumnEnhancements:
    id: uuid.UUID
    description: str | None
    tags: list[str]
    is_sensitive: bool
    version: int


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_enhancement", message, {"field": field})


def _text(value: str | None, label: str, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise _invalid(f"The {label} must be at most {max_length} characters.", field)
    return cleaned or None


def _tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            raise _invalid("A tag must not be empty.", "tags")
        if len(tag) > TAG_MAX_LENGTH:
            raise _invalid(f"A tag must be at most {TAG_MAX_LENGTH} characters.", "tags")
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS:
        raise _invalid(f"An object has at most {MAX_TAGS} tags.", "tags")
    return cleaned


def _classification(value: str | None) -> str | None:
    if value is not None and value not in CLASSIFICATIONS:
        raise _invalid(
            f"The classification must be one of {', '.join(CLASSIFICATIONS)}.", "classification"
        )
    return value


def _snapshot(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: list(v) if isinstance(v := getattr(record, f), list) else v for f in fields}


def set_fields(
    record: SrcTableRecord | SrcColumnRecord, entity_type: str, changes: Mapping[str, Any]
) -> None:
    """Validate ``changes`` and set them on ``record`` (no version check, audit or flush)."""
    for field, value in changes.items():
        if field == "description":
            record.description = _text(value, "description", field, DESCRIPTION_MAX_LENGTH)
        elif field == "tags":
            record.tags = _tags(value)
        elif field == "is_sensitive":
            record.is_sensitive = bool(value)
        elif field == "classification" and isinstance(record, SrcTableRecord):
            record.classification = _classification(value)
        elif field == "scd_hint" and isinstance(record, SrcTableRecord):
            record.scd_hint = _text(value, "SCD hint", field, SCD_HINT_MAX_LENGTH)
        else:  # pragma: no cover - a programming error
            raise ValueError(f"cannot change {entity_type} field {field!r}")


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class EnhancementService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def update_table(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> TableEnhancements:
        """Change a table's enhancements; ``changes`` holds only the fields to change. A
        stale ``version`` is 409 ``version_conflict``; 422 ``invalid_enhancement``."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            record = db.scalars(
                sa.select(SrcTableRecord)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(
                    SrcTableRecord.id == table_id, SrcDbSchemaRecord.source_system_id == system_id
                )
                .with_for_update()
            ).first()
            if record is None:
                raise _not_found("Table")
            self._apply(db, user, system, record, "source_table", TABLE_FIELDS, version, changes)
            return TableEnhancements(
                id=record.id,
                version=record.version,
                **_snapshot(record, TABLE_FIELDS),
            )

    def update_column(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> ColumnEnhancements:
        """Change a column's enhancements (see ``update_table``)."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            record = db.scalars(
                sa.select(SrcColumnRecord)
                .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(
                    SrcColumnRecord.id == column_id,
                    SrcColumnRecord.table_id == table_id,
                    SrcDbSchemaRecord.source_system_id == system_id,
                )
                .with_for_update(of=SrcColumnRecord)
            ).first()
            if record is None:
                raise _not_found("Column")
            self._apply(db, user, system, record, "source_column", COLUMN_FIELDS, version, changes)
            return ColumnEnhancements(
                id=record.id,
                version=record.version,
                **_snapshot(record, COLUMN_FIELDS),
            )

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        system = db.get(SourceSystemRecord, system_id)
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    def _apply(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        record: SrcTableRecord | SrcColumnRecord,
        entity_type: str,
        fields: tuple[str, ...],
        version: int,
        changes: Mapping[str, Any],
    ) -> None:
        if record.version != version:
            raise ApiError(
                409,
                "version_conflict",
                "Someone else changed this object since you loaded it. Reload and try again.",
                {"current_version": record.version},
            )
        before = _snapshot(record, fields)
        set_fields(record, entity_type, changes)
        after = _snapshot(record, fields)
        changed = [f for f in fields if before[f] != after[f]]
        if not changed:
            return
        record.version += 1
        now = self._clock()
        db.flush()
        record_audit(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            entity_type=entity_type,
            entity_id=record.id,
            old={f: before[f] for f in changed},
            new={f: after[f] for f in changed},
            at=now,
        )
        record_activity(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            verb=f"{entity_type}.enhanced",
            object_type=entity_type,
            object_id=record.id,
            object_label=record.name,
            details={"source_system_id": str(system.id), "fields": changed},
            at=now,
        )
