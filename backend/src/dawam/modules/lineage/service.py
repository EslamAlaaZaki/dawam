"""The lineage graph and impact report over the stored lineage edges (spec §6.14, stories
79, 110, 111, 112).

The ``warehouse`` module stores the edges and walks them; this module puts a name on every
node, whichever module owns it: source columns (``sources``), DW columns and tables
(``warehouse``) and KPIs (``kpis``). Only nodes of the Workspace are named, and an edge is
shown only when both its ends are.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.kpis import kpi_names
from dawam.modules.sources import confirmed_pii_column_ids, source_column_labels
from dawam.modules.warehouse import LineageEdge, lineage_edges, lineage_nodes, propagate_pii
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError

Direction = Literal["upstream", "downstream", "both"]
NodeType = Literal["src_column", "dw_column", "dw_table", "kpi"]

MAX_DEPTH = 64
"""The deepest walk; the full lineage of a KPI back to source columns is far shorter."""

LAYER_ORDER = {"source": 0, "staging": 1, "core": 2, "mart": 3, "kpi": 4}


@dataclass(frozen=True)
class Node:
    id: uuid.UUID
    type: NodeType
    label: str
    layer: str
    """``source``, a DW Layer (``staging``, ``core``, ``mart``) or ``kpi``."""


@dataclass(frozen=True)
class Edge:
    id: uuid.UUID
    kind: str
    """``value``, ``uses``, ``lookup`` or ``kpi``."""
    from_id: uuid.UUID
    to_id: uuid.UUID


@dataclass(frozen=True)
class Graph:
    start: Node
    nodes: list[Node]
    edges: list[Edge]


@dataclass(frozen=True)
class Impact:
    start: Node
    columns: list[Node]
    """DW columns its values reach, and those of the tables it steers that feed further."""
    tables: list[Node]
    """DW tables steered by it: a join, filter or GROUP BY (``uses``) or a lookup."""
    kpis: list[Node]


@dataclass(frozen=True)
class PiiView:
    columns: list[Node]
    """PII-derived DW columns: fed by a confirmed PII column through value edges."""
    tables: list[Node]
    """PII-influenced DW tables: PII only steers them (join, filter, lookup)."""


def _order(node: Node) -> tuple[int, str]:
    return (LAYER_ORDER.get(node.layer, 9), node.label)


class LineageService:
    """Every method authorizes through the workspaces policy first (any member reads)."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService) -> None:
        self._engine = engine
        self._workspaces = workspaces

    def graph(
        self,
        user: User,
        workspace_id: uuid.UUID,
        node_id: uuid.UUID,
        direction: Direction = "both",
        depth: int = MAX_DEPTH,
    ) -> Graph:
        """The nodes and edges on a path of at most ``depth`` edges to (``upstream``) or
        from (``downstream``) a node, or both. 404 ``not_found`` for a node outside the
        Workspace."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            start = self._start(db, workspace_id, node_id)
            edges = lineage_edges(db, node_id, direction, min(depth, MAX_DEPTH))
            nodes = self._nodes(db, workspace_id, edges) | {start.id: start}
        shown = [
            Edge(e.id, e.kind, e.from_id, e.to_id)
            for e in edges
            if e.from_id in nodes and e.to_id in nodes
        ]
        shown.sort(key=lambda e: (e.kind, nodes[e.from_id].label, nodes[e.to_id].label))
        return Graph(start, sorted(nodes.values(), key=_order), shown)

    def impact(self, user: User, workspace_id: uuid.UUID, node_id: uuid.UUID) -> Impact:
        """Everything downstream of a node (typically a source column): the DW columns its
        values reach, the DW tables it steers and the KPIs that depend on it."""
        graph = self.graph(user, workspace_id, node_id, "downstream")
        unique = [n for n in graph.nodes if n.id != node_id]
        return Impact(
            start=graph.start,
            columns=sorted((n for n in unique if n.type == "dw_column"), key=_order),
            tables=sorted((n for n in unique if n.type == "dw_table"), key=_order),
            kpis=sorted((n for n in unique if n.type == "kpi"), key=_order),
        )

    def pii_view(self, user: User, workspace_id: uuid.UUID) -> PiiView:
        """The PII-derived DW columns and, separately, the PII-influenced DW tables (spec
        §6.12, story 137), computed from the lineage edges as they stand now."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            result = propagate_pii(db, confirmed_pii_column_ids(db, workspace_id))
            nodes = lineage_nodes(
                db, workspace_id, result.derived_columns | result.influenced_tables
            )
        found = [Node(n.id, n.type, n.label, n.layer) for n in nodes.values()]  # type: ignore[arg-type]
        return PiiView(
            columns=sorted((n for n in found if n.type == "dw_column"), key=_order),
            tables=sorted((n for n in found if n.type == "dw_table"), key=_order),
        )

    def _start(self, db: Session, workspace_id: uuid.UUID, node_id: uuid.UUID) -> Node:
        found = self._label(db, workspace_id, [node_id])
        if node_id not in found:
            raise ApiError(404, "not_found", "Lineage node not found.")
        return found[node_id]

    def _nodes(
        self, db: Session, workspace_id: uuid.UUID, edges: list[LineageEdge]
    ) -> dict[uuid.UUID, Node]:
        return self._label(db, workspace_id, {i for e in edges for i in (e.from_id, e.to_id)})

    def _label(
        self, db: Session, workspace_id: uuid.UUID, ids: list[uuid.UUID] | set[uuid.UUID]
    ) -> dict[uuid.UUID, Node]:
        wanted = list(ids)
        nodes: dict[uuid.UUID, Node] = {
            i: Node(i, n.type, n.label, n.layer)  # type: ignore[arg-type]
            for i, n in lineage_nodes(db, workspace_id, wanted).items()
        }
        rest = [i for i in wanted if i not in nodes]
        for i, label in source_column_labels(db, workspace_id, rest).items():
            nodes[i] = Node(i, "src_column", label, "source")
        rest = [i for i in rest if i not in nodes]
        for i, name in kpi_names(db, workspace_id, rest).items():
            nodes[i] = Node(i, "kpi", name, "kpi")
        return nodes
