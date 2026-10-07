"""Graph queries over the stored lineage edges (spec §6.14).

Recursive CTEs with a cycle guard: each walk carries the edges it has taken and never
takes one twice, so a self-referencing hierarchy (employee to manager) ends. A ``uses``
edge points at the mapped table and so reaches every column of it: walking up from a
column also takes its table's ``uses`` edges, and walking down through a table
continues from its columns.
"""

from __future__ import annotations

import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

MAX_DEPTH = 64
"""A safety net beside the cycle guard; Layers are three deep."""

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


def reachable_edge_ids(
    db: Session, column_id: uuid.UUID, direction: Literal["upstream", "downstream"]
) -> list[uuid.UUID]:
    """The ids of every edge on a path to (upstream) or from (downstream) a DW column."""
    query = _UPSTREAM if direction == "upstream" else _DOWNSTREAM
    rows = db.execute(query, {"start": column_id, "max_depth": MAX_DEPTH})
    return [row[0] for row in rows]
