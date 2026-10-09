"""Graph queries over the stored lineage edges (spec §6.14).

Recursive CTEs with a cycle guard: each walk carries the edges it has taken and never
takes one twice, so a self-referencing hierarchy (employee to manager) ends. A ``uses``
edge points at the mapped table and so reaches every column of it: walking up from a
column also takes its table's ``uses`` edges, and walking down through (or from) a table
continues from its columns. A walk stops after ``depth`` edges.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import DataWarehouseRecord, DwColumnRecord, DwTableRecord, LineageEdgeRecord

MAX_DEPTH = 64
"""A safety net beside the cycle guard (Layers are three deep), and the deepest walk a
caller may ask for."""

# Each step is one recursive term; its LATERAL step is two index-friendly branches (the
# node itself, and its table's ``uses`` edges) rather than an ``OR`` the planner cannot index.
_UPSTREAM = sa.text("""
    WITH RECURSIVE walk(edge_id, from_id, path) AS (
        SELECT e.id, e.from_id, ARRAY[e.id] FROM lineage_edges e WHERE e.to_id = :start
        UNION ALL
        SELECT e.id, e.from_id, ARRAY[e.id]
          FROM dw_columns c JOIN lineage_edges e ON e.to_id = c.table_id
         WHERE c.id = :start
        UNION ALL
        SELECT s.id, s.from_id, w.path || s.id
          FROM walk w
         CROSS JOIN LATERAL (
              SELECT e.id, e.from_id FROM lineage_edges e WHERE e.to_id = w.from_id
              UNION ALL
              SELECT e.id, e.from_id
                FROM dw_columns c JOIN lineage_edges e ON e.to_id = c.table_id
               WHERE c.id = w.from_id
         ) s
         WHERE NOT s.id = ANY(w.path) AND cardinality(w.path) < :max_depth
    )
    SELECT DISTINCT edge_id FROM walk
""")

_DOWNSTREAM = sa.text("""
    WITH RECURSIVE walk(edge_id, to_id, path) AS (
        SELECT e.id, e.to_id, ARRAY[e.id] FROM lineage_edges e WHERE e.from_id = :start
        UNION ALL
        SELECT e.id, e.to_id, ARRAY[e.id]
          FROM dw_columns c JOIN lineage_edges e ON e.from_id = c.id
         WHERE c.table_id = :start
        UNION ALL
        SELECT s.id, s.to_id, w.path || s.id
          FROM walk w
         CROSS JOIN LATERAL (
              SELECT e.id, e.to_id FROM lineage_edges e WHERE e.from_id = w.to_id
              UNION ALL
              SELECT e.id, e.to_id
                FROM dw_columns c JOIN lineage_edges e ON e.from_id = c.id
               WHERE c.table_id = w.to_id
         ) s
         WHERE NOT s.id = ANY(w.path) AND cardinality(w.path) < :max_depth
    )
    SELECT DISTINCT edge_id FROM walk
""")


Direction = Literal["upstream", "downstream", "both"]


@dataclass(frozen=True)
class LineageEdge:
    id: uuid.UUID
    kind: str
    from_type: str
    from_id: uuid.UUID
    to_type: str
    to_id: uuid.UUID


def reachable_edge_ids(
    db: Session,
    node_id: uuid.UUID,
    direction: Direction,
    depth: int = MAX_DEPTH,
) -> list[uuid.UUID]:
    """The ids of every edge on a path of at most ``depth`` edges to (upstream) or from
    (downstream) a node: a source or DW column, a DW table or a KPI."""
    params = {"start": node_id, "max_depth": min(depth, MAX_DEPTH)}
    queries = {"upstream": [_UPSTREAM], "downstream": [_DOWNSTREAM]}.get(
        direction, [_UPSTREAM, _DOWNSTREAM]
    )
    return list(dict.fromkeys(row[0] for query in queries for row in db.execute(query, params)))


def lineage_edges(
    db: Session, node_id: uuid.UUID, direction: Direction, depth: int = MAX_DEPTH
) -> list[LineageEdge]:
    """The edges :func:`reachable_edge_ids` finds, in the caller's session."""
    ids = reachable_edge_ids(db, node_id, direction, depth)
    if not ids:
        return []
    return [
        LineageEdge(e.id, e.kind, e.from_type, e.from_id, e.to_type, e.to_id)
        for e in db.scalars(sa.select(LineageEdgeRecord).where(LineageEdgeRecord.id.in_(ids)))
    ]


@dataclass(frozen=True)
class LineageNode:
    id: uuid.UUID
    type: str
    """``dw_column`` or ``dw_table`` here; ``src_column`` and ``kpi`` come from their modules."""
    label: str
    """``table.column`` for a column, the name for a table."""
    layer: str


def lineage_nodes(
    db: Session, workspace_id: uuid.UUID, node_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, LineageNode]:
    """The given ids that are DW columns or tables of the Workspace's Data Warehouse."""
    ids = list(node_ids)
    if not ids:
        return {}
    in_workspace = sa.and_(
        DataWarehouseRecord.id == DwTableRecord.data_warehouse_id,
        DataWarehouseRecord.workspace_id == workspace_id,
    )
    nodes: dict[uuid.UUID, LineageNode] = {}
    for column_id, column, table, layer in db.execute(
        sa.select(DwColumnRecord.id, DwColumnRecord.name, DwTableRecord.name, DwTableRecord.layer)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .join(DataWarehouseRecord, in_workspace)
        .where(DwColumnRecord.id.in_(ids))
    ):
        nodes[column_id] = LineageNode(column_id, "dw_column", f"{table}.{column}", layer)
    for table_id, table, layer in db.execute(
        sa.select(DwTableRecord.id, DwTableRecord.name, DwTableRecord.layer)
        .join(DataWarehouseRecord, in_workspace)
        .where(DwTableRecord.id.in_(ids))
    ):
        nodes[table_id] = LineageNode(table_id, "dw_table", table, layer)
    return nodes
