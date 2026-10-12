"""The warehouse's part of soft deletes (spec §7 "Soft deletes"), in the caller's session and
without a permission check: the ``deletions`` module authorizes and runs a permanent delete
in one transaction.

A soft-deleted table (``status = deleted``, see ``ModelService.delete_table``) is hidden from
the model, the mappings, validation and the score; here it is listed, its impact is read and
it is deleted for good. A deleted Source System's Staging Tables, and their mappings, go with
it; the Core branches that read them stay, and validation reports them as broken
(``broken_reference``), so a table fed by another system keeps its other branches.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit

from .lineage_sql import Unparsable, parse_branch
from .mapping_service import SOURCE_LAYER, purge_edges_reading
from .tables import (
    DELETED,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    TableMappingRecord,
    TombstoneRecord,
)

_UPPER = {below: layer for layer, below in SOURCE_LAYER.items()}
"""The Layer that reads each Layer (``staging`` → ``core``, ``core`` → ``mart``)."""


@dataclass(frozen=True)
class DeletedDwTable:
    id: uuid.UUID
    layer: str
    name: str
    kind: str
    column_count: int
    deleted_at: datetime


@dataclass(frozen=True)
class DwTableRef:
    id: uuid.UUID
    layer: str
    name: str
    has_mapping: bool
    """It has a mapping of its own, which goes with it."""


@dataclass(frozen=True)
class BrokenBranch:
    """A mapping branch that reads a table which is going: it stays, and becomes broken."""

    table_id: uuid.UUID
    table_name: str
    layer: str
    branch_id: uuid.UUID
    branch_name: str


@dataclass(frozen=True)
class DwTableDeletion:
    """What the permanent delete of a soft-deleted table takes with it."""

    table: DeletedDwTable
    column_ids: list[uuid.UUID]
    column_names: list[str]
    has_mapping: bool
    broken_branches: list[BrokenBranch]


@dataclass(frozen=True)
class StagingDeletion:
    """A deleted Source System's Staging Tables (with their staging mappings) and the
    branches that read them."""

    staging_tables: list[DwTableRef]
    column_ids: list[uuid.UUID]
    broken_branches: list[BrokenBranch]


def _warehouse(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord | None:
    return db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()


def _column_count() -> sa.ScalarSelect[int]:
    return (
        sa.select(sa.func.count())
        .where(DwColumnRecord.table_id == DwTableRecord.id)
        .scalar_subquery()
    )


def _deleted(table: DwTableRecord, columns: int) -> DeletedDwTable:
    return DeletedDwTable(table.id, table.layer, table.name, table.kind, columns, table.updated_at)


def deleted_dw_tables(db: Session, workspace_id: uuid.UUID) -> list[DeletedDwTable]:
    """The Workspace's soft-deleted DW tables, by Layer and name."""
    warehouse = _warehouse(db, workspace_id)
    if warehouse is None:
        return []
    rows = db.execute(
        sa.select(DwTableRecord, _column_count())
        .where(DwTableRecord.data_warehouse_id == warehouse.id, DwTableRecord.status == DELETED)
        .order_by(DwTableRecord.layer, sa.func.lower(DwTableRecord.name), DwTableRecord.id)
    ).all()
    return [_deleted(t, n) for t, n in rows]


def _deleted_table(
    db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID, *, lock: bool = False
) -> DwTableRecord | None:
    query = (
        sa.select(DwTableRecord)
        .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
        .where(
            DataWarehouseRecord.workspace_id == workspace_id,
            DwTableRecord.id == table_id,
            DwTableRecord.status == DELETED,
        )
    )
    if lock:
        query = query.with_for_update(of=DwTableRecord)
    return db.scalars(query).first()


def _has_mapping(db: Session, table_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    if not table_ids:
        return set()
    return set(
        db.scalars(
            sa.select(TableMappingRecord.dw_table_id).where(
                TableMappingRecord.dw_table_id.in_(list(table_ids))
            )
        )
    )


def branches_reading(
    db: Session, warehouse: DataWarehouseRecord, layer: str, names: Collection[str]
) -> list[BrokenBranch]:
    """The branches of the Layer above ``layer`` (of tables that are not deleted) whose SQL
    reads one of the ``layer`` tables ``names``."""
    upper = _UPPER.get(layer)
    wanted = {n.lower() for n in names}
    if upper is None or not wanted:
        return []
    found: list[BrokenBranch] = []
    for branch, table in db.execute(
        sa.select(MappingBranchRecord, DwTableRecord)
        .join(TableMappingRecord, TableMappingRecord.id == MappingBranchRecord.table_mapping_id)
        .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer == upper,
            DwTableRecord.status != DELETED,
        )
        .order_by(sa.func.lower(DwTableRecord.name), MappingBranchRecord.ordinal)
    ):
        try:
            parts = parse_branch(
                branch.driving_input,
                branch.joins,
                branch.filters,
                branch.group_by,
                branch.having,
                warehouse.target_platform,
            )
        except Unparsable:
            continue
        if wanted.intersection(parts.tables):
            found.append(BrokenBranch(table.id, table.name, table.layer, branch.id, branch.name))
    return found


def dw_table_deletion(
    db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID
) -> DwTableDeletion | None:
    """What deleting a soft-deleted table for good takes with it; ``None`` if it is not a
    soft-deleted table of the Workspace."""
    table = _deleted_table(db, workspace_id, table_id)
    if table is None:
        return None
    columns = list(
        db.execute(
            sa.select(DwColumnRecord.id, DwColumnRecord.name)
            .where(DwColumnRecord.table_id == table.id)
            .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
        )
    )
    warehouse = db.get(DataWarehouseRecord, table.data_warehouse_id)
    assert warehouse is not None
    return DwTableDeletion(
        table=_deleted(table, len(columns)),
        column_ids=[i for i, _ in columns],
        column_names=[n for _, n in columns],
        has_mapping=bool(_has_mapping(db, [table.id])),
        broken_branches=branches_reading(db, warehouse, table.layer, [table.name]),
    )


def _table_snapshot(table: DwTableRecord) -> dict[str, object]:
    return {
        "layer": table.layer,
        "name": table.name,
        "kind": table.kind,
        "status": table.status,
        "description": table.description,
    }


def _delete_tables(
    db: Session,
    workspace_id: uuid.UUID,
    tables: Sequence[DwTableRecord],
    *,
    actor_id: uuid.UUID,
    at: datetime,
) -> None:
    ids = [t.id for t in tables]
    if not ids:
        return
    column_ids = list(
        db.scalars(sa.select(DwColumnRecord.id).where(DwColumnRecord.table_id.in_(ids)))
    )
    purge_edges_reading(db, column_ids)
    # A foreign key left pointing at a table that goes (only deleted tables can still have
    # one) is cleared: the reference has no ``ondelete``.
    db.execute(
        sa.update(DwColumnRecord)
        .where(DwColumnRecord.references_table_id.in_(ids), DwColumnRecord.table_id.not_in(ids))
        .values(references_table_id=None)
    )
    for table in tables:
        record_audit(
            db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            entity_type="dw_table",
            entity_id=table.id,
            old=_table_snapshot(table),
            new=None,
            at=at,
        )
        db.delete(table)
    db.flush()


def purge_dw_table(
    db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID, *, actor_id: uuid.UUID, at: datetime
) -> DeletedDwTable | None:
    """Delete a soft-deleted table for good, with its columns and mapping; ``None`` if it is
    not a soft-deleted table of the Workspace. Its Tombstone (a Staging Table or a generated
    table) stays, so sync and regeneration never bring it back."""
    table = _deleted_table(db, workspace_id, table_id, lock=True)
    if table is None:
        return None
    columns = db.scalar(sa.select(sa.func.count()).where(DwColumnRecord.table_id == table.id))
    view = _deleted(table, columns or 0)
    _delete_tables(db, workspace_id, [table], actor_id=actor_id, at=at)
    return view


def _staging_of(
    db: Session, workspace_id: uuid.UUID, source_table_ids: Collection[uuid.UUID], *, lock: bool
) -> tuple[DataWarehouseRecord | None, list[DwTableRecord]]:
    warehouse = _warehouse(db, workspace_id)
    if warehouse is None or not source_table_ids:
        return warehouse, []
    query = (
        sa.select(DwTableRecord)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer == "staging",
            DwTableRecord.source_table_id.in_(list(source_table_ids)),
        )
        .order_by(sa.func.lower(DwTableRecord.name), DwTableRecord.id)
    )
    if lock:
        query = query.with_for_update()
    return warehouse, list(db.scalars(query))


def staging_deletion(
    db: Session, workspace_id: uuid.UUID, source_table_ids: Collection[uuid.UUID]
) -> StagingDeletion:
    """The Staging Tables of the given Source Tables (any state), whether each has a
    mapping, and the Core branches that read them."""
    warehouse, tables = _staging_of(db, workspace_id, source_table_ids, lock=False)
    if warehouse is None or not tables:
        return StagingDeletion([], [], [])
    mapped = _has_mapping(db, [t.id for t in tables])
    column_ids = list(
        db.scalars(
            sa.select(DwColumnRecord.id).where(DwColumnRecord.table_id.in_([t.id for t in tables]))
        )
    )
    return StagingDeletion(
        staging_tables=[DwTableRef(t.id, t.layer, t.name, t.id in mapped) for t in tables],
        column_ids=column_ids,
        broken_branches=branches_reading(db, warehouse, "staging", [t.name for t in tables]),
    )


def purge_staging(
    db: Session,
    workspace_id: uuid.UUID,
    source_table_ids: Collection[uuid.UUID],
    *,
    actor_id: uuid.UUID,
    at: datetime,
) -> None:
    """Delete the Staging Tables of the given Source Tables (a deleted Source System's) for
    good, with their columns and staging mappings, and drop their Tombstones: the Source
    Tables they stood for are going too. Branches reading them stay, broken."""
    warehouse, tables = _staging_of(db, workspace_id, source_table_ids, lock=True)
    if warehouse is None:
        return
    _delete_tables(db, workspace_id, tables, actor_id=actor_id, at=at)
    if source_table_ids:
        db.execute(
            sa.delete(TombstoneRecord).where(
                TombstoneRecord.data_warehouse_id == warehouse.id,
                TombstoneRecord.object_type == "staging_table",
                TombstoneRecord.src_object_id.in_(list(source_table_ids)),
            )
        )
