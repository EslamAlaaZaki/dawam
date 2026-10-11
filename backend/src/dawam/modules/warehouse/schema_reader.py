"""The DW Schema as other modules read it, and the lineage edges they own (spec §6.13, §6.14).

The KPIs module validates formula SQL against the Core and Mart tables and links KPIs to
their columns; the ``kpi`` lineage edges (DW column to KPI) live in this module's
``lineage_edges`` table, so these functions write them. All run in the caller's session
(one transaction with the caller's own writes) and do no permission checks: the caller
authorized first.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
)

KPI_LAYERS = ("core", "mart")
"""The Layers a KPI formula or link may name (Staging holds raw copies)."""


@dataclass(frozen=True)
class SchemaColumn:
    id: uuid.UUID
    table_id: uuid.UUID
    table_name: str
    layer: str
    name: str
    role: str
    additivity: str | None
    references_table_id: uuid.UUID | None = None
    """A foreign key's dimension (a table of the same Layer)."""


@dataclass(frozen=True)
class SchemaTable:
    id: uuid.UUID
    layer: str
    name: str
    kind: str
    columns: tuple[SchemaColumn, ...]


@dataclass(frozen=True)
class DwSchema:
    """The Core and Mart tables of a Workspace's Data Warehouse."""

    platform: str
    layer_schemas: dict[str, str]
    """Layer to physical schema name."""
    tables: tuple[SchemaTable, ...]


def _warehouse(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord | None:
    return db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()


def _column(row: tuple[DwColumnRecord, DwTableRecord]) -> SchemaColumn:
    column, table = row
    return SchemaColumn(
        id=column.id,
        table_id=table.id,
        table_name=table.name,
        layer=table.layer,
        name=column.name,
        role=column.role,
        additivity=column.additivity,
        references_table_id=column.references_table_id,
    )


def read_schema(db: Session, workspace_id: uuid.UUID) -> DwSchema | None:
    """The Core and Mart tables with their columns; ``None`` before the DW is set up."""
    warehouse = _warehouse(db, workspace_id)
    if warehouse is None:
        return None
    tables = list(
        db.scalars(
            sa.select(DwTableRecord)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer.in_(KPI_LAYERS),
            )
            .order_by(DwTableRecord.layer, DwTableRecord.name)
        )
    )
    columns: dict[uuid.UUID, list[SchemaColumn]] = {t.id: [] for t in tables}
    for column in db.scalars(
        sa.select(DwColumnRecord)
        .where(DwColumnRecord.table_id.in_(columns))
        .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
    ):
        table = next(t for t in tables if t.id == column.table_id)
        columns[table.id].append(_column((column, table)))
    return DwSchema(
        platform=warehouse.target_platform,
        layer_schemas=dict(warehouse.layer_physical_schemas),
        tables=tuple(
            SchemaTable(t.id, t.layer, t.name, t.kind, tuple(columns[t.id])) for t in tables
        ),
    )


def describe_columns(
    db: Session, workspace_id: uuid.UUID, column_ids: Iterable[uuid.UUID]
) -> list[SchemaColumn]:
    """The given columns that are Core or Mart columns of the Workspace's Data Warehouse
    (others are left out), ordered by Layer, table and position."""
    ids = list(column_ids)
    warehouse = _warehouse(db, workspace_id)
    if warehouse is None or not ids:
        return []
    rows = db.execute(
        sa.select(DwColumnRecord, DwTableRecord)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            DwColumnRecord.id.in_(ids),
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer.in_(KPI_LAYERS),
        )
        .order_by(DwTableRecord.layer, DwTableRecord.name, DwColumnRecord.ordinal)
    ).all()
    return [_column((c, t)) for c, t in rows]


def mart_columns_reading(db: Session, core_column_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """For each Core column a Mart column is mapped from (a ``value`` edge), that Mart
    column as ``table.column``: a KPI links to the highest Layer holding the measure."""
    ids = list(core_column_ids)
    if not ids:
        return {}
    rows = db.execute(
        sa.select(LineageEdgeRecord.from_id, DwTableRecord.name, DwColumnRecord.name)
        .join(DwColumnRecord, DwColumnRecord.id == LineageEdgeRecord.to_id)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            LineageEdgeRecord.kind == "value",
            LineageEdgeRecord.from_type == "dw_column",
            LineageEdgeRecord.to_type == "dw_column",
            LineageEdgeRecord.from_id.in_(ids),
            DwTableRecord.layer == "mart",
        )
        .order_by(DwTableRecord.name, DwColumnRecord.name)
    )
    found: dict[uuid.UUID, str] = {}
    for from_id, table_name, column_name in rows:
        found.setdefault(from_id, f"{table_name}.{column_name}")
    return found


def replace_kpi_edges(db: Session, kpi_id: uuid.UUID, column_ids: Iterable[uuid.UUID]) -> None:
    """Make the KPI's ``kpi`` lineage edges exactly one per given DW column."""
    clear_kpi_edges(db, kpi_id)
    db.add_all(
        LineageEdgeRecord(
            id=uuid.uuid4(),
            kind="kpi",
            from_type="dw_column",
            from_id=column_id,
            to_type="kpi",
            to_id=kpi_id,
        )
        for column_id in dict.fromkeys(column_ids)
    )
    db.flush()


def clear_kpi_edges(db: Session, kpi_id: uuid.UUID) -> None:
    """Delete every ``kpi`` lineage edge that ends at the KPI."""
    db.execute(
        sa.delete(LineageEdgeRecord).where(
            LineageEdgeRecord.kind == "kpi", LineageEdgeRecord.to_id == kpi_id
        )
    )


UNMAPPED_TYPES = ("unmapped", "not_in_branch")
"""Mapping types that fill nothing."""


@dataclass(frozen=True)
class MappingState:
    """How the Core and Mart columns are filled, for the KPI suggestions (spec §6.13)."""

    value_edges: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    """``(from column, to column)`` for every ``value`` edge into a Core or Mart column."""
    mapped: frozenset[uuid.UUID]
    """Core and Mart columns with a mapping that fills them, and every column of a Generated
    Table (DAWAM fills those itself)."""


def read_mapping_state(db: Session, workspace_id: uuid.UUID) -> MappingState:
    """The ``value`` edges and mapped columns of the Core and Mart Layers (empty before the
    Data Warehouse is set up)."""
    warehouse = _warehouse(db, workspace_id)
    if warehouse is None:
        return MappingState((), frozenset())
    in_scope = sa.and_(
        DwTableRecord.data_warehouse_id == warehouse.id, DwTableRecord.layer.in_(KPI_LAYERS)
    )
    edges = db.execute(
        sa.select(LineageEdgeRecord.from_id, LineageEdgeRecord.to_id)
        .join(DwColumnRecord, DwColumnRecord.id == LineageEdgeRecord.to_id)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            in_scope,
            LineageEdgeRecord.kind == "value",
            LineageEdgeRecord.from_type == "dw_column",
            LineageEdgeRecord.to_type == "dw_column",
        )
    ).all()
    filled = db.scalars(
        sa.select(DwColumnRecord.id)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .outerjoin(ColumnMappingRecord, ColumnMappingRecord.dw_column_id == DwColumnRecord.id)
        .where(
            in_scope,
            sa.or_(
                DwTableRecord.kind == "generated",
                ColumnMappingRecord.mapping_type.not_in(UNMAPPED_TYPES),
            ),
        )
    )
    return MappingState(tuple((a, b) for a, b in edges), frozenset(filled))
