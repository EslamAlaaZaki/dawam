"""Impact analysis for change propagation (spec §6.10, stories 53, 113).

Propagation is computed **after** a structural change was applied, from what was applied:
``plan_propagation`` takes the changes (``StructuralChange``) and returns the Change Set
items that carry them through lineage to the Layers above, plus the impacted Assets to name
in the alert. The rule table:

- **Rename** (column or table): mapping expressions, lookup inputs and branch joins, filters,
  GROUP BY and HAVING that name it are rewritten to the new name (sqlglot finds the
  identifiers; the rest of the text stays as written). A downstream column of the same name
  fed by a ``value`` edge gets an optional rename item, and what reads *that* column follows
  it (its items depend on the optional one), so the change travels through Core and Mart.
- **Type change**: a downstream column mapped ``direct`` from it gets an optional retype item,
  transitively; anything else it feeds is named (type-compatibility warnings stay in
  validation).
- **Nullability** (now nullable): downstream non-null columns and keys it feeds are named;
  nothing is proposed.
- **Delete / ``source_removed``**: never cascades. Each column mapping that reads it is
  proposed as ``unmapped``; a branch whose joins or filters read it is named and stays a
  validation error.
- **New input**: no propagation (creates are never a ``StructuralChange``).

KPIs are another module's: the plan carries what they need (renames to respell in formula
SQL, removed names to flag, Core columns a Mart column is now mapped from, for re-pointing).
Everything runs in the caller's session and authorizes nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import ProposedItem

from .lineage_graph import lineage_edges, lineage_nodes
from .lineage_sql import (
    BRANCH_PARTS,
    Renames,
    branch_reads,
    expression_reads,
    rename_in_branch,
    rename_in_expression,
)
from .schema_reader import SchemaColumn, describe_columns
from .tables import (
    DELETED,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

logger = logging.getLogger(__name__)

StructuralKind = Literal["rename", "retype", "nullability", "removed", "mapped"]

UPPER = {"staging": "core", "core": "mart"}
"""The Layer mapped from a Layer."""
LAYER_ORDER = ("staging", "core", "mart")
KEY_ROLES = ("sk", "nk", "fk")


@dataclass(frozen=True)
class StructuralChange:
    """A structural change already made to a DW table or column.

    ``rename``: ``old``/``new`` names. ``retype``: ``old``/``new`` neutral types.
    ``nullability``: ``old``/``new`` ``is_nullable``. ``removed``: deleted, or flagged
    ``source_removed``. ``mapped``: a Mart column's mapping was saved (KPIs linked to the
    Core column it reads may be re-pointed). A deleted object no longer exists, so its
    ``layer``, ``table_name`` and ``column_name`` (``None`` for a table) are given."""

    object_type: Literal["dw_table", "dw_column"]
    object_id: uuid.UUID
    kind: StructuralKind
    old: Any = None
    new: Any = None
    layer: str = ""
    table_name: str = ""
    column_name: str | None = None


class StructuralChangeHook(Protocol):
    def __call__(
        self, user: User, workspace_id: uuid.UUID, changes: Sequence[StructuralChange]
    ) -> None:
        """Called after ``user``'s structural changes were committed, outside their
        transaction. The ``propagation`` module imports this one, so the editor reaches it
        through this port, which the composition root fills."""
        ...


def notify_structural_change(
    hook: StructuralChangeHook | None,
    user: User,
    workspace_id: uuid.UUID,
    changes: Sequence[StructuralChange],
) -> None:
    """Call ``hook`` (if any) with ``changes`` (if any); what it raises is logged, since the
    change stands either way."""
    if hook is None or not changes:
        return
    try:
        hook(user, workspace_id, changes)
    except Exception:
        logger.exception("propagating a change failed", extra={"workspace_id": str(workspace_id)})


def same_type(a: Any, b: Any) -> bool:
    """Whether two neutral types are the same, however many of their keys are spelled out."""
    keys = ("type", "length", "precision", "scale")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a == b
    return all(a.get(k) == b.get(k) for k in keys)


@dataclass(frozen=True)
class Impact:
    """An Asset a change reaches, for the alert."""

    layer: str
    """``staging``, ``core`` or ``mart``."""
    object_type: str
    """``dw_table`` or ``dw_column``."""
    object_id: uuid.UUID
    label: str
    """``table.column`` or the table name."""


@dataclass(frozen=True)
class KpiRename:
    """Names a KPI's formula SQL must respell, in a Layer, once ``depends_on`` (an item key;
    ``None``: already applied) is accepted."""

    layer: str
    renames: Renames
    depends_on: str | None


@dataclass(frozen=True)
class KpiRemoval:
    """A removed Core or Mart table or column, for flagging the KPIs whose formula names it."""

    layer: str
    names: Renames


@dataclass
class Propagation:
    items: list[tuple[str, ProposedItem]] = field(default_factory=list)
    """Each item with the Layer it belongs to, dependencies first."""
    impacts: list[Impact] = field(default_factory=list)
    reached: list[uuid.UUID] = field(default_factory=list)
    """Every node id downstream of the changes (DW columns and tables, KPIs)."""
    kpi_renames: list[KpiRename] = field(default_factory=list)
    kpi_removals: list[KpiRemoval] = field(default_factory=list)
    repoints: list[tuple[SchemaColumn, SchemaColumn]] = field(default_factory=list)
    """(Core column, the Mart column now mapped ``direct`` from it)."""
    layer_schemas: dict[str, str] = field(default_factory=dict)
    platform: str = ""


@dataclass
class _MappingEdit:
    layer: str
    label: str
    payload: dict[str, Any]
    depends_on: set[str]


class _Planner:
    def __init__(self, db: Session, warehouse: DataWarehouseRecord) -> None:
        self.db = db
        self.warehouse = warehouse
        self.platform = warehouse.target_platform
        self.out = Propagation(
            layer_schemas=dict(warehouse.layer_physical_schemas), platform=self.platform
        )
        self.column_items: list[tuple[str, ProposedItem]] = []
        self.keys: set[str] = set()
        self.mapping_edits: dict[str, _MappingEdit] = {}
        self.mapping_order: list[str] = []
        self.queue: list[tuple[StructuralChange, str | None]] = []
        self.starts: list[uuid.UUID] = []
        self.direct_targets: list[uuid.UUID] = []
        self.flagged: dict[uuid.UUID, None] = {}
        self._mappings: dict[str, list[tuple[ColumnMappingRecord, DwColumnRecord, DwTableRecord]]]
        self._mappings = {}
        self._branches: dict[str, list[tuple[MappingBranchRecord, DwTableRecord]]] = {}

    # -- reading ----------------------------------------------------------------------

    def mappings(
        self, layer: str
    ) -> list[tuple[ColumnMappingRecord, DwColumnRecord, DwTableRecord]]:
        if layer not in self._mappings:
            self._mappings[layer] = [
                (m, c, t)
                for m, c, t in self.db.execute(
                    sa.select(ColumnMappingRecord, DwColumnRecord, DwTableRecord)
                    .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
                    .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
                    .where(
                        DwTableRecord.data_warehouse_id == self.warehouse.id,
                        DwTableRecord.layer == layer,
                        DwTableRecord.status != DELETED,
                    )
                    .order_by(DwTableRecord.name, DwColumnRecord.ordinal, ColumnMappingRecord.id)
                )
            ]
        return self._mappings[layer]

    def branches(self, layer: str) -> list[tuple[MappingBranchRecord, DwTableRecord]]:
        if layer not in self._branches:
            self._branches[layer] = [
                (b, t)
                for b, t in self.db.execute(
                    sa.select(MappingBranchRecord, DwTableRecord)
                    .join(
                        TableMappingRecord,
                        TableMappingRecord.id == MappingBranchRecord.table_mapping_id,
                    )
                    .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
                    .where(
                        DwTableRecord.data_warehouse_id == self.warehouse.id,
                        DwTableRecord.layer == layer,
                        DwTableRecord.status != DELETED,
                    )
                    .order_by(DwTableRecord.name, MappingBranchRecord.ordinal)
                )
            ]
        return self._branches[layer]

    def column(self, column_id: uuid.UUID) -> tuple[DwColumnRecord, DwTableRecord] | None:
        row = self.db.execute(
            sa.select(DwColumnRecord, DwTableRecord)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .where(
                DwColumnRecord.id == column_id,
                DwTableRecord.data_warehouse_id == self.warehouse.id,
            )
        ).first()
        return None if row is None else (row[0], row[1])

    def table(self, table_id: uuid.UUID) -> DwTableRecord | None:
        return self.db.scalars(
            sa.select(DwTableRecord).where(
                DwTableRecord.id == table_id,
                DwTableRecord.data_warehouse_id == self.warehouse.id,
            )
        ).first()

    def fed(self, column_id: uuid.UUID) -> list[tuple[DwColumnRecord, DwTableRecord, str]]:
        """The columns ``column_id`` feeds through a ``value`` edge, with their mapping type."""
        rows = self.db.execute(
            sa.select(DwColumnRecord, DwTableRecord, ColumnMappingRecord.mapping_type)
            .select_from(LineageEdgeRecord)
            .join(DwColumnRecord, DwColumnRecord.id == LineageEdgeRecord.to_id)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .join(ColumnMappingRecord, ColumnMappingRecord.id == LineageEdgeRecord.mapping_id)
            .where(
                LineageEdgeRecord.kind == "value",
                LineageEdgeRecord.from_id == column_id,
                LineageEdgeRecord.to_type == "dw_column",
            )
            .order_by(DwTableRecord.name, DwColumnRecord.ordinal)
        ).all()
        seen: dict[uuid.UUID, tuple[DwColumnRecord, DwTableRecord, str]] = {}
        for column, table, mapping_type in rows:
            if column.id in seen and seen[column.id][2] == "direct":
                continue
            seen[column.id] = (column, table, mapping_type)
        return list(seen.values())

    # -- planning ---------------------------------------------------------------------

    def run(self, changes: Sequence[StructuralChange]) -> Propagation:
        self.queue = [(c, None) for c in changes]
        while self.queue:
            change, depends_on = self.queue.pop(0)
            if change.kind == "rename":
                self.renamed(change, depends_on)
            elif change.kind == "retype":
                self.retyped(change, depends_on)
            elif change.kind == "nullability":
                self.nullable(change)
            elif change.kind == "removed":
                self.removed(change)
            elif change.kind == "mapped":
                self.mapped(change)
        return self.finish(changes)

    def renamed(self, change: StructuralChange, depends_on: str | None) -> None:
        if change.object_type == "dw_table":
            table = self.table(change.object_id)
            if table is None:
                return
            layer = table.layer
            names = Renames(tables={str(change.old).lower(): str(change.new)}, columns={})
        else:
            found = self.column(change.object_id)
            if found is None:
                return
            table = found[1]
            layer = table.layer
            names = Renames(
                tables={}, columns={(table.name.lower(), str(change.old).lower()): str(change.new)}
            )
        self.starts.append(change.object_id)
        upper = UPPER.get(layer)
        if upper is not None:
            self.rewrite(upper, names, depends_on)
        if layer in ("core", "mart"):
            self.out.kpi_renames.append(
                KpiRename(layer, self.with_schema(names, layer), depends_on)
            )
        if change.object_type != "dw_column" or upper is None:
            return
        old = str(change.old).lower()
        for downstream, down_table, _ in self.fed(change.object_id):
            if downstream.name.lower() != old or down_table.layer != upper:
                continue
            if self.name_taken(down_table.id, str(change.new), downstream.id):
                continue
            key = f"rename:{downstream.id}"
            if key in self.keys:
                continue
            self.add_column_item(
                down_table.layer,
                ProposedItem(
                    key=key,
                    object_type="dw_column",
                    operation="update",
                    object_id=downstream.id,
                    label=(
                        f"{down_table.layer}.{down_table.name}.{downstream.name}: rename to "
                        f"{change.new} (optional)"
                    ),
                    payload={"name": str(change.new)},
                    depends_on=[depends_on] if depends_on else [],
                ),
            )
            self.queue.append(
                (
                    StructuralChange(
                        "dw_column", downstream.id, "rename", downstream.name, change.new
                    ),
                    key,
                )
            )

    def retyped(self, change: StructuralChange, depends_on: str | None) -> None:
        if change.object_type != "dw_column":
            return
        found = self.column(change.object_id)
        if found is None:
            return
        self.starts.append(change.object_id)
        upper = UPPER.get(found[1].layer)
        for downstream, down_table, mapping_type in self.fed(change.object_id):
            if down_table.layer != upper or mapping_type != "direct":
                self.flag(downstream.id)
                continue
            if same_type(downstream.data_type, change.new):
                continue
            key = f"retype:{downstream.id}"
            if key in self.keys:
                continue
            self.add_column_item(
                down_table.layer,
                ProposedItem(
                    key=key,
                    object_type="dw_column",
                    operation="update",
                    object_id=downstream.id,
                    label=(
                        f"{down_table.layer}.{down_table.name}.{downstream.name}: retype to "
                        f"{_type_label(change.new)} (optional)"
                    ),
                    payload={"data_type": change.new},
                    depends_on=[depends_on] if depends_on else [],
                ),
            )
            self.queue.append(
                (
                    StructuralChange(
                        "dw_column", downstream.id, "retype", downstream.data_type, change.new
                    ),
                    key,
                )
            )

    def nullable(self, change: StructuralChange) -> None:
        if change.object_type != "dw_column" or not change.new or change.old:
            return  # only a column that became nullable can break what it feeds
        self.starts.append(change.object_id)
        for downstream, _, _ in self.fed(change.object_id):
            if not downstream.is_nullable or downstream.role in KEY_ROLES:
                self.flag(downstream.id)

    def removed(self, change: StructuralChange) -> None:
        layer, table_name, column_name = change.layer, change.table_name, change.column_name
        if change.object_type == "dw_table":
            table = self.table(change.object_id)
            if table is not None:
                layer, table_name = table.layer, table.name
                self.starts.append(table.id)
            names = Renames(tables={table_name.lower(): table_name}, columns={})
        else:
            found = self.column(change.object_id)
            if found is not None:
                layer, table_name, column_name = found[1].layer, found[1].name, found[0].name
                self.starts.append(found[0].id)
            key = (table_name.lower(), str(column_name).lower())
            names = Renames(tables={}, columns={key: str(column_name)})
        if layer in ("core", "mart"):
            self.out.kpi_removals.append(KpiRemoval(layer, self.with_schema(names, layer)))
        upper = UPPER.get(layer)
        if upper is None:
            return
        for record, column, table in self.mappings(upper):
            if record.mapping_type == "unmapped" or not self.reads(record, names):
                continue
            edit = self.mapping_edit(record, column, table)
            edit.payload = {"mapping_type": "unmapped", "sql_expression": "", "lookup": None}
            edit.label = f"{_mapping_label(table, column, record)}: unmap (reads a removed input)"
            self.direct_targets.append(column.id)
        for branch, table in self.branches(upper):
            if branch_reads(_parts(branch), self.platform, names):
                self.flag(table.id)

    def mapped(self, change: StructuralChange) -> None:
        found = self.column(change.object_id)
        if found is None or found[1].layer != "mart":
            return
        rows = self.db.execute(
            sa.select(LineageEdgeRecord.from_id)
            .join(ColumnMappingRecord, ColumnMappingRecord.id == LineageEdgeRecord.mapping_id)
            .where(
                LineageEdgeRecord.kind == "value",
                LineageEdgeRecord.to_id == found[0].id,
                ColumnMappingRecord.mapping_type == "direct",
            )
        )
        sources = list(dict.fromkeys(rows.scalars()))
        workspace_id = self.warehouse.workspace_id
        described = {
            c.id: c for c in describe_columns(self.db, workspace_id, [*sources, found[0].id])
        }
        mart = described.get(found[0].id)
        for source in sources:
            core = described.get(source)
            if core is not None and core.layer == "core" and mart is not None:
                self.out.repoints.append((core, mart))

    # -- items ------------------------------------------------------------------------

    def rewrite(self, layer: str, names: Renames, depends_on: str | None) -> None:
        """Respell ``names`` in every mapping of ``layer`` that names them."""
        for record, column, table in self.mappings(layer):
            edit = self.mapping_edits.get(f"mapping:{record.id}")
            current = edit.payload if edit is not None else {}
            if current.get("mapping_type") == "unmapped":
                continue
            payload: dict[str, Any] = {}
            sql = current.get("sql_expression", record.sql_expression) or ""
            if sql:
                new_sql = rename_in_expression(sql, self.platform, names)
                if new_sql != sql:
                    payload["sql_expression"] = new_sql
            lookup = current.get("lookup", record.lookup)
            if lookup:
                new_lookup = _rename_lookup(lookup, self.platform, names)
                if new_lookup != lookup:
                    payload["lookup"] = new_lookup
            if not payload:
                continue
            edit = self.mapping_edit(record, column, table)
            edit.payload.update(payload)
            edit.label = f"{_mapping_label(table, column, record)}: follow the rename"
            if depends_on:
                edit.depends_on.add(depends_on)
        for branch, table in self.branches(layer):
            key = f"branch:{branch.id}"
            edit = self.mapping_edits.get(key)
            parts = {**_parts(branch), **(edit.payload if edit else {})}
            renamed = rename_in_branch(parts, self.platform, names)
            changed = {p: renamed[p] for p in BRANCH_PARTS if renamed[p] != parts[p]}
            if not changed:
                continue
            if edit is None:
                edit = _MappingEdit(
                    table.layer,
                    f"{table.layer}.{table.name} ({branch.name}): follow the rename",
                    {},
                    set(),
                )
                self.mapping_edits[key] = edit
                self.mapping_order.append(key)
            edit.payload.update(changed)
            if depends_on:
                edit.depends_on.add(depends_on)

    def mapping_edit(
        self, record: ColumnMappingRecord, column: DwColumnRecord, table: DwTableRecord
    ) -> _MappingEdit:
        key = f"mapping:{record.id}"
        if key not in self.mapping_edits:
            self.mapping_edits[key] = _MappingEdit(
                table.layer, _mapping_label(table, column, record), {}, set()
            )
            self.mapping_order.append(key)
        return self.mapping_edits[key]

    def reads(self, record: ColumnMappingRecord, names: Renames) -> bool:
        if record.sql_expression and expression_reads(record.sql_expression, self.platform, names):
            return True
        return any(
            expression_reads(text, self.platform, names) for text in _lookup_inputs(record.lookup)
        )

    def add_column_item(self, layer: str, item: ProposedItem) -> None:
        self.keys.add(item.key)
        self.column_items.append((layer, item))

    def name_taken(self, table_id: uuid.UUID, name: str, own_id: uuid.UUID) -> bool:
        return bool(
            self.db.scalar(
                sa.select(sa.func.count())
                .select_from(DwColumnRecord)
                .where(
                    DwColumnRecord.table_id == table_id,
                    sa.func.lower(DwColumnRecord.name) == name.lower(),
                    DwColumnRecord.id != own_id,
                )
            )
        )

    def flag(self, node_id: uuid.UUID) -> None:
        self.flagged[node_id] = None

    def with_schema(self, names: Renames, layer: str) -> Renames:
        schema = self.out.layer_schemas.get(layer)
        return Renames(names.tables, names.columns, schema.lower() if schema else None)

    def finish(self, changes: Sequence[StructuralChange]) -> Propagation:
        mapping_items: list[tuple[str, ProposedItem]] = []
        for key in self.mapping_order:
            edit = self.mapping_edits[key]
            object_type, object_id = key.split(":", 1)
            mapping_items.append(
                (
                    edit.layer,
                    ProposedItem(
                        key=key,
                        object_type="column_mapping"
                        if object_type == "mapping"
                        else ("mapping_branch"),
                        operation="update",
                        object_id=uuid.UUID(object_id),
                        label=edit.label,
                        payload=edit.payload,
                        depends_on=sorted(edit.depends_on),
                    ),
                )
            )
        rank = {layer: i for i, layer in enumerate(LAYER_ORDER)}
        ordered = sorted(
            [(0, i, layer, item) for i, (layer, item) in enumerate(self.column_items)]
            + [(1, i, layer, item) for i, (layer, item) in enumerate(mapping_items)],
            key=lambda entry: (rank[entry[2]], entry[0], entry[1]),
        )
        self.out.items = [(layer, item) for _, _, layer, item in ordered]
        # What the changes reach: everything downstream of them in lineage, the mappings
        # proposed or flagged above and what is downstream of those.
        reached: dict[uuid.UUID, None] = {}
        own = {c.object_id for c in changes}
        for start in [*self.starts, *self.direct_targets, *self.flagged]:
            if start not in own:
                reached[start] = None
            for edge in lineage_edges(self.db, start, "downstream"):
                reached[edge.to_id] = None
        for _, item in self.out.items:
            if item.object_type == "dw_column" and item.object_id is not None:
                reached[item.object_id] = None
        for key in self.mapping_order:
            if key.startswith("branch:"):
                continue
            mapping = self.db.get(ColumnMappingRecord, uuid.UUID(key.split(":", 1)[1]))
            if mapping is not None:
                reached[mapping.dw_column_id] = None
        for node_id in own:
            reached.pop(node_id, None)
        self.out.reached = list(reached)
        nodes = lineage_nodes(self.db, self.warehouse.workspace_id, reached)
        self.out.impacts = sorted(
            (Impact(n.layer, n.type, n.id, n.label) for n in nodes.values()),
            key=lambda i: (rank.get(i.layer, 9), i.label),
        )
        return self.out


def _type_label(data_type: Any) -> str:
    if not isinstance(data_type, dict):
        return str(data_type)
    kind = data_type.get("type", "?")
    if data_type.get("length"):
        return f"{kind}({data_type['length']})"
    if data_type.get("precision") is not None:
        return f"{kind}({data_type['precision']},{data_type.get('scale') or 0})"
    return str(kind)


def _mapping_label(
    table: DwTableRecord, column: DwColumnRecord, record: ColumnMappingRecord
) -> str:
    where = " (branch)" if record.branch_id else ""
    return f"{table.layer}.{table.name}.{column.name} mapping{where}"


def _parts(branch: MappingBranchRecord) -> dict[str, str | None]:
    return {part: getattr(branch, part) for part in BRANCH_PARTS}


def _lookup_inputs(lookup: dict[str, Any] | None) -> list[str]:
    if not lookup:
        return []
    texts = list(lookup.get("nk_inputs") or [])
    if lookup.get("as_of_input"):
        texts.append(lookup["as_of_input"])
    return [t for t in texts if isinstance(t, str)]


def _rename_lookup(lookup: dict[str, Any], platform: str, names: Renames) -> dict[str, Any]:
    renamed = dict(lookup)
    renamed["nk_inputs"] = [
        rename_in_expression(t, platform, names) if isinstance(t, str) else t
        for t in lookup.get("nk_inputs") or []
    ]
    if lookup.get("as_of_input"):
        renamed["as_of_input"] = rename_in_expression(lookup["as_of_input"], platform, names)
    return renamed


def plan_propagation(
    db: Session, workspace_id: uuid.UUID, changes: Sequence[StructuralChange]
) -> Propagation:
    """What ``changes`` (already applied) call for downstream, in the caller's session. An
    empty plan before the Data Warehouse is set up."""
    warehouse = db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()
    if warehouse is None or not changes:
        return Propagation()
    return _Planner(db, warehouse).run(changes)
