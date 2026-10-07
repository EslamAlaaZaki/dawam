"""Builds the query guard's view of a Snapshot (ADR 0002): its tables, views and columns, each
column's protection by the one policy (``pii.protected_column_ids``), and the view definitions
the guard traces. Reads only app-database rows."""

from __future__ import annotations

import uuid
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..tables import (
    DefinitionTextRecord,
    SnapshotColumnRecord,
    SnapshotTableRecord,
)
from .pii import protected_column_ids
from .query_guard import GuardCatalog, GuardColumn, GuardTable


def load_guard_catalog(
    db: Session, *, snapshot_id: uuid.UUID, database: str, allowed_schemas: tuple[str, ...]
) -> GuardCatalog:
    """The guard's catalog for ``snapshot_id`` (a Source System's latest Snapshot)."""
    tables = list(
        db.execute(
            sa.select(
                SnapshotTableRecord.src_table_id,
                SnapshotTableRecord.db_schema,
                SnapshotTableRecord.name,
                SnapshotTableRecord.kind,
                DefinitionTextRecord.text,
            )
            .outerjoin(
                DefinitionTextRecord,
                DefinitionTextRecord.hash == SnapshotTableRecord.view_definition_hash,
            )
            .where(SnapshotTableRecord.snapshot_id == snapshot_id)
        )
    )
    columns: dict[uuid.UUID, list[tuple[int, uuid.UUID, str]]] = defaultdict(list)
    for table_id, column_id, name, ordinal in db.execute(
        sa.select(
            SnapshotColumnRecord.src_table_id,
            SnapshotColumnRecord.src_column_id,
            SnapshotColumnRecord.name,
            SnapshotColumnRecord.ordinal,
        ).where(SnapshotColumnRecord.snapshot_id == snapshot_id)
    ):
        columns[table_id].append((ordinal, column_id, name))
    protected = protected_column_ids(db, [cid for cols in columns.values() for _, cid, _ in cols])
    return GuardCatalog(
        database=database,
        allowed_schemas=tuple(allowed_schemas),
        tables=tuple(
            GuardTable(
                schema=schema,
                name=name,
                kind=kind,
                definition=definition,
                columns=tuple(
                    GuardColumn(col_name, col_id in protected)
                    for _, col_id, col_name in sorted(columns[table_id])
                ),
            )
            for table_id, schema, name, kind, definition in tables
        ),
    )
