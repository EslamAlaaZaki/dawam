"""What "Fix impacts" recomputes propagation from (spec §6.10, story 113a).

``open_impacts`` reads the Core and Mart mappings as they are now, like the validation's
``broken_reference`` and ``removed_input`` errors do, and turns each error propagation can
fix back into the ``StructuralChange`` that caused it:

- a mapping or branch naming a table or column of the Layer below that no longer exists by
  that name, but that an object there was **renamed** from (its former names come from the
  audit trail): a ``rename`` from the name it reads to the object's current name;
- naming a column that no longer exists (and was not renamed) of a table that does: a
  ``removed`` column;
- naming a table or column flagged ``source_removed``: a ``removed`` one.

A table that is gone without a rename is left as it is: nothing tells what it was. Expired
Change Set items are never read here, so they are recomputed, never revived. Runs in the
caller's session and authorizes nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .lineage_sql import ColumnRef, Unparsable, parse_branch, parse_expression
from .mapping_service import SOURCE_LAYER
from .propagation import StructuralChange, _lookup_inputs
from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

RENAMED_ENTITY_TYPES = ("dw_table", "dw_column", "staging_table", "staging_column")
"""The audit entity types whose renames ``open_impacts`` reads."""


class _Layer:
    """The tables and columns of one Layer, by current and former name."""

    def __init__(self) -> None:
        self.tables: dict[str, DwTableRecord] = {}
        self.columns: dict[uuid.UUID, dict[str, DwColumnRecord]] = {}
        self.former_tables: dict[str, DwTableRecord] = {}
        self.former_columns: dict[uuid.UUID, dict[str, DwColumnRecord]] = {}


def open_impacts(
    db: Session, workspace_id: uuid.UUID, former_names: Sequence[tuple[uuid.UUID, str]]
) -> list[StructuralChange]:
    """The structural changes behind the open errors propagation can fix, each once.
    ``former_names`` are ``(DW table or column id, a name it had)``, oldest first (a later
    rename from the same name wins)."""
    warehouse = db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()
    if warehouse is None:
        return []
    layers: dict[str, _Layer] = {}
    tables_by_id: dict[uuid.UUID, DwTableRecord] = {}
    columns_by_id: dict[uuid.UUID, DwColumnRecord] = {}
    for table, column in db.execute(
        sa.select(DwTableRecord, DwColumnRecord)
        .outerjoin(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
        .where(DwTableRecord.data_warehouse_id == warehouse.id)
    ):
        layer = layers.setdefault(table.layer, _Layer())
        layer.tables[table.name.lower()] = table
        tables_by_id[table.id] = table
        if column is not None:
            layer.columns.setdefault(table.id, {})[column.name.lower()] = column
            columns_by_id[column.id] = column
    for object_id, name in former_names:
        if object_id in tables_by_id:
            table = tables_by_id[object_id]
            layers[table.layer].former_tables[name.lower()] = table
        elif object_id in columns_by_id:
            column = columns_by_id[object_id]
            table = tables_by_id[column.table_id]
            layers[table.layer].former_columns.setdefault(table.id, {})[name.lower()] = column

    found: dict[tuple[uuid.UUID, str, str], StructuralChange] = {}

    def add(change: StructuralChange) -> None:
        found.setdefault((change.object_id, change.kind, str(change.old)), change)

    def resolve(below: str, name: str) -> DwTableRecord | None:
        """The table of the Layer below that ``name`` means: itself, or what it became."""
        layer = layers.get(below)
        if layer is None:
            return None
        table = layer.tables.get(name)
        if table is not None:
            return table
        table = layer.former_tables.get(name)
        if table is not None:
            add(StructuralChange("dw_table", table.id, "rename", name, table.name))
        return table

    def check(below: str, refs: Iterable[ColumnRef]) -> None:
        for ref in refs:
            if ref.table is None:
                continue
            table = resolve(below, ref.table)
            if table is None:
                continue
            if table.status == "source_removed":
                add(StructuralChange("dw_table", table.id, "removed"))
                continue
            column = layers[below].columns.get(table.id, {}).get(ref.column)
            if column is None:
                column = layers[below].former_columns.get(table.id, {}).get(ref.column)
                if column is not None:
                    add(StructuralChange("dw_column", column.id, "rename", ref.column, column.name))
                else:
                    add(
                        StructuralChange(
                            "dw_column",
                            uuid.uuid5(table.id, ref.column),
                            "removed",
                            layer=below,
                            table_name=table.name,
                            column_name=ref.column,
                        )
                    )
                    continue
            if column.status == "source_removed":
                add(StructuralChange("dw_column", column.id, "removed"))

    platform = warehouse.target_platform
    for record, table in db.execute(
        sa.select(ColumnMappingRecord, DwTableRecord)
        .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer.in_(tuple(SOURCE_LAYER)),
            DwTableRecord.kind != "generated",
            ColumnMappingRecord.mapping_type != "unmapped",
        )
        .order_by(DwTableRecord.name, DwColumnRecord.ordinal, ColumnMappingRecord.id)
    ):
        if record.validation.get("unparsed"):
            continue  # SQL that does not parse: no propagation fixes it
        # Other stored errors don't stop it: an accepted propagation that re-derived the
        # mapping stores ``broken_reference`` for an input still missing, which is exactly
        # what this recomputes.
        texts = [record.sql_expression] if record.sql_expression else []
        for text in [*texts, *_lookup_inputs(record.lookup)]:
            try:
                parsed = parse_expression(text, platform)
            except Unparsable:
                continue
            check(SOURCE_LAYER[table.layer], [*parsed.value, *parsed.uses])
    for branch, table in db.execute(
        sa.select(MappingBranchRecord, DwTableRecord)
        .join(TableMappingRecord, TableMappingRecord.id == MappingBranchRecord.table_mapping_id)
        .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer.in_(tuple(SOURCE_LAYER)),
        )
        .order_by(DwTableRecord.name, MappingBranchRecord.ordinal)
    ):
        below = SOURCE_LAYER[table.layer]
        try:
            parts = parse_branch(
                branch.driving_input,
                branch.joins,
                branch.filters,
                branch.group_by,
                branch.having,
                platform,
            )
        except Unparsable:
            continue
        for name in parts.tables:
            resolved = resolve(below, name)
            if resolved is not None and resolved.status == "source_removed":
                add(StructuralChange("dw_table", resolved.id, "removed"))
        check(below, parts.refs)
    return list(found.values())
