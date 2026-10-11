"""What the permanent delete of a soft-deleted Source System takes with it (spec §7 "Soft
deletes"), in the caller's session and without a permission check: the ``deletions``
module authorizes and runs the whole delete in one transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .service import SourceSystem, _view
from .tables import (
    SnapshotRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcRoutineRecord,
    SrcTableRecord,
)


@dataclass(frozen=True)
class SourceSystemContents:
    """The Source Objects and Snapshots that exist only because of a Source System."""

    table_ids: list[uuid.UUID]
    column_ids: list[uuid.UUID]
    db_schema_count: int
    table_count: int
    column_count: int
    routine_count: int
    snapshot_count: int


def deleted_source_systems(db: Session, workspace_id: uuid.UUID) -> list[SourceSystem]:
    """The Workspace's soft-deleted Source Systems, by System Code."""
    return [
        _view(r)
        for r in db.scalars(
            sa.select(SourceSystemRecord)
            .where(
                SourceSystemRecord.workspace_id == workspace_id,
                SourceSystemRecord.status == "deleted",
            )
            .order_by(SourceSystemRecord.code)
        )
    ]


def deleted_source_system(
    db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID, *, lock: bool = False
) -> SourceSystem | None:
    """A soft-deleted Source System of the Workspace, or ``None`` (none, another
    Workspace's, or not deleted)."""
    query = sa.select(SourceSystemRecord).where(
        SourceSystemRecord.id == system_id,
        SourceSystemRecord.workspace_id == workspace_id,
        SourceSystemRecord.status == "deleted",
    )
    if lock:
        query = query.with_for_update()
    record = db.scalars(query).first()
    return None if record is None else _view(record)


def source_system_contents(db: Session, system_id: uuid.UUID) -> SourceSystemContents:
    """The system's Source Objects (every state) and Snapshots."""
    schema_ids = sa.select(SrcDbSchemaRecord.id).where(
        SrcDbSchemaRecord.source_system_id == system_id
    )
    table_ids = list(
        db.scalars(
            sa.select(SrcTableRecord.id)
            .where(SrcTableRecord.db_schema_id.in_(schema_ids))
            .order_by(SrcTableRecord.id)
        )
    )
    column_ids = (
        list(
            db.scalars(
                sa.select(SrcColumnRecord.id)
                .where(SrcColumnRecord.table_id.in_(table_ids))
                .order_by(SrcColumnRecord.id)
            )
        )
        if table_ids
        else []
    )

    def count(query: sa.Select) -> int:
        return db.scalar(sa.select(sa.func.count()).select_from(query.subquery())) or 0

    return SourceSystemContents(
        table_ids=table_ids,
        column_ids=column_ids,
        db_schema_count=count(schema_ids),
        table_count=len(table_ids),
        column_count=len(column_ids),
        routine_count=count(
            sa.select(SrcRoutineRecord.id).where(SrcRoutineRecord.db_schema_id.in_(schema_ids))
        ),
        snapshot_count=count(
            sa.select(SnapshotRecord.id).where(SnapshotRecord.source_system_id == system_id)
        ),
    )


def purge_source_system(db: Session, system_id: uuid.UUID) -> None:
    """Delete the system for good. Its Connection, Source Objects, Snapshots, profiles,
    relationships and PII findings go with it (foreign keys cascade); the caller has
    already moved or removed what other modules keep for it (KPIs, files, staging)."""
    db.execute(sa.delete(SourceSystemRecord).where(SourceSystemRecord.id == system_id))
