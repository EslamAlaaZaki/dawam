"""What staging generation reads of the Source Schema (spec §6.7), and the one thing it writes.

For the warehouse module, which has already authorized the caller: the base tables of every
present Source System (and the views an editor opted in), with their columns as of the latest
Snapshot, and the stable placeholder numbers that stand in for non-Latin names. A placeholder
number is assigned once, on the Source Object, and never changes (it is not derived from
ordering, so it is the same in every Snapshot).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import (
    ConnectionRecord,
    SnapshotColumnRecord,
    SnapshotRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)


@dataclass(frozen=True)
class StagingColumn:
    id: uuid.UUID
    name: str
    data_type: str
    is_nullable: bool
    ordinal: int
    placeholder_no: int | None
    status: str = "present"
    """``source_removed`` only when a read asked for removed objects."""


@dataclass(frozen=True)
class StagingTable:
    id: uuid.UUID
    db_schema_id: uuid.UUID
    db_schema: str
    name: str
    kind: str
    placeholder_no: int | None
    columns: list[StagingColumn]
    status: str = "present"


@dataclass(frozen=True)
class StagingSystem:
    id: uuid.UUID
    code: str
    engine: str | None
    """The Connection's engine; ``None`` for a system fed only by Schema Imports."""
    tables: list[StagingTable]


class StagingSourceService:
    """No permission checks: the caller (the warehouse module) authorized already."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def read(
        self,
        workspace_id: uuid.UUID,
        *,
        system_id: uuid.UUID | None = None,
        include_removed: bool = False,
    ) -> list[StagingSystem]:
        """Present Source Systems (or just ``system_id``) with the tables staging covers:
        base tables, and views with ``include_view_in_staging``; only objects still present
        in the source, plus the ``source_removed`` ones when ``include_removed`` (sync needs
        them to flag their Staging Tables)."""
        with Session(self._engine) as db:
            systems = db.execute(
                sa.select(SourceSystemRecord.id, SourceSystemRecord.code, ConnectionRecord.engine)
                .outerjoin(
                    ConnectionRecord, ConnectionRecord.source_system_id == SourceSystemRecord.id
                )
                .where(
                    SourceSystemRecord.workspace_id == workspace_id,
                    SourceSystemRecord.status == "present",
                    *([SourceSystemRecord.id == system_id] if system_id else []),
                )
                .order_by(SourceSystemRecord.code)
            ).all()
            result: list[StagingSystem] = []
            for system_id, code, engine in systems:
                tables = self._tables(db, system_id, include_removed)
                result.append(StagingSystem(system_id, code, engine, tables))
            return result

    def _tables(
        self, db: Session, system_id: uuid.UUID, include_removed: bool
    ) -> list[StagingTable]:
        wanted = ("present", "source_removed") if include_removed else ("present",)
        table_rows = db.execute(
            sa.select(
                SrcTableRecord.id,
                SrcDbSchemaRecord.id,
                SrcDbSchemaRecord.name,
                SrcTableRecord.name,
                SrcTableRecord.kind,
                SrcTableRecord.placeholder_no,
                SrcTableRecord.status,
            )
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(
                SrcDbSchemaRecord.source_system_id == system_id,
                SrcDbSchemaRecord.status.in_(wanted),
                SrcTableRecord.status.in_(wanted),
                sa.or_(SrcTableRecord.kind == "table", SrcTableRecord.include_view_in_staging),
            )
            .order_by(SrcDbSchemaRecord.name, SrcTableRecord.name, SrcTableRecord.id)
        ).all()
        columns: dict[uuid.UUID, list[StagingColumn]] = {}
        latest = (
            sa.select(SnapshotRecord.id)
            .where(SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest)
            .scalar_subquery()
        )
        for row in db.execute(
            sa.select(
                SrcColumnRecord.id,
                SrcColumnRecord.table_id,
                SrcColumnRecord.name,
                SrcColumnRecord.current_definition,
                SrcColumnRecord.placeholder_no,
                SrcColumnRecord.status,
                SnapshotColumnRecord.ordinal,
            )
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .outerjoin(
                SnapshotColumnRecord,
                sa.and_(
                    SnapshotColumnRecord.src_column_id == SrcColumnRecord.id,
                    SnapshotColumnRecord.snapshot_id == latest,
                ),
            )
            .where(
                SrcDbSchemaRecord.source_system_id == system_id,
                SrcColumnRecord.status.in_(wanted),
            )
        ):
            definition = row.current_definition
            columns.setdefault(row.table_id, []).append(
                StagingColumn(
                    id=row.id,
                    name=row.name,
                    data_type=str(definition.get("data_type") or ""),
                    is_nullable=bool(definition.get("is_nullable", True)),
                    ordinal=row.ordinal if row.ordinal is not None else 1_000_000,
                    placeholder_no=row.placeholder_no,
                    status=row.status,
                )
            )
        return [
            StagingTable(
                id=table_id,
                db_schema_id=schema_id,
                db_schema=schema_name,
                name=name,
                kind=kind,
                placeholder_no=placeholder_no,
                columns=sorted(columns.get(table_id, []), key=lambda c: (c.ordinal, c.name)),
                status=status,
            )
            for table_id, schema_id, schema_name, name, kind, placeholder_no, status in table_rows
        ]

    def assign_placeholders(
        self,
        workspace_id: uuid.UUID,
        *,
        tables: Iterable[uuid.UUID] = (),
        columns: Iterable[uuid.UUID] = (),
    ) -> None:
        """Give each listed table and column that has none the next placeholder number: per
        Source System for tables, per table for columns. Numbers already assigned stay."""
        wanted_tables, wanted_columns = set(tables), set(columns)
        if not wanted_tables and not wanted_columns:
            return
        with Session(self._engine) as db, db.begin():
            if wanted_tables:
                self._number_tables(db, workspace_id, wanted_tables)
            if wanted_columns:
                self._number_columns(db, workspace_id, wanted_columns)

    def _number_tables(self, db: Session, workspace_id: uuid.UUID, wanted: set[uuid.UUID]) -> None:
        rows = db.execute(
            sa.select(SrcTableRecord, SrcDbSchemaRecord.source_system_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
            .where(SrcTableRecord.id.in_(wanted), SourceSystemRecord.workspace_id == workspace_id)
            .order_by(SrcTableRecord.name, SrcTableRecord.id)
        ).all()
        for system_id in sorted({r[1] for r in rows}, key=str):
            # Serialises concurrent numbering of one system.
            db.execute(
                sa.select(SourceSystemRecord.id)
                .where(SourceSystemRecord.id == system_id)
                .with_for_update()
            )
            top = db.scalar(
                sa.select(sa.func.coalesce(sa.func.max(SrcTableRecord.placeholder_no), 0))
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(SrcDbSchemaRecord.source_system_id == system_id)
            )
            for record, owner in rows:
                if owner == system_id and record.placeholder_no is None:
                    top = (top or 0) + 1
                    record.placeholder_no = top

    def _number_columns(self, db: Session, workspace_id: uuid.UUID, wanted: set[uuid.UUID]) -> None:
        rows = (
            db.execute(
                sa.select(SrcColumnRecord)
                .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .join(
                    SourceSystemRecord,
                    SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id,
                )
                .where(
                    SrcColumnRecord.id.in_(wanted), SourceSystemRecord.workspace_id == workspace_id
                )
                .order_by(SrcColumnRecord.name, SrcColumnRecord.id)
            )
            .scalars()
            .all()
        )
        tops: dict[uuid.UUID, int] = {}
        for record in rows:
            if record.table_id not in tops:
                tops[record.table_id] = (
                    db.scalar(
                        sa.select(
                            sa.func.coalesce(sa.func.max(SrcColumnRecord.placeholder_no), 0)
                        ).where(SrcColumnRecord.table_id == record.table_id)
                    )
                    or 0
                )
            if record.placeholder_no is None:
                tops[record.table_id] += 1
                record.placeholder_no = tops[record.table_id]


def source_column_labels(
    db: Session, workspace_id: uuid.UUID, column_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """``SYSTEM.schema.table.column`` for each given id that is a source column of the
    Workspace (others are left out); in the caller's session, no permission check."""
    ids = list(column_ids)
    if not ids:
        return {}
    rows = db.execute(
        sa.select(
            SrcColumnRecord.id,
            SourceSystemRecord.code,
            SrcDbSchemaRecord.name,
            SrcTableRecord.name,
            SrcColumnRecord.name,
        )
        .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
        .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
        .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
        .where(SrcColumnRecord.id.in_(ids), SourceSystemRecord.workspace_id == workspace_id)
    )
    return {
        column_id: ".".join(part for part in (system, schema, table, column) if part)
        for column_id, system, schema, table, column in rows
    }
