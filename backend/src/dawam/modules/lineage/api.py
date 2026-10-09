"""The lineage graph and impact report (spec §6.14, stories 79, 110, 111, 112):
``GET /workspaces/{id}/data-warehouse/lineage?node=&direction=&depth=`` and
``GET /workspaces/{id}/data-warehouse/impact?node=``.

Handlers only translate HTTP to ``LineageService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .service import MAX_DEPTH, Direction, LineageService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse", tags=["lineage"])


def lineage_service(request: Request) -> LineageService:
    state = request.app.state
    workspaces = WorkspaceService(state.engine, clock=state.services.clock)
    return LineageService(state.engine, workspaces=workspaces)


LineageServiceDep = Annotated[LineageService, Depends(lineage_service)]


class LineageNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: Literal["src_column", "dw_column", "dw_table", "kpi"]
    label: str
    """`SYSTEM.schema.table.column` for a source column, `table.column` for a DW column,
    the name for a DW table or KPI."""
    layer: Literal["source", "staging", "core", "mart", "kpi"]


class LineageGraphEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["value", "uses", "lookup", "kpi"]
    from_id: uuid.UUID
    to_id: uuid.UUID


class NodeLineage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: LineageNode
    nodes: list[LineageNode]
    edges: list[LineageGraphEdge]


class ImpactReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start: LineageNode
    columns: list[LineageNode]
    """DW columns its values reach."""
    tables: list[LineageNode]
    """DW tables it steers through a join, filter or GROUP BY (`uses`) or a lookup."""
    kpis: list[LineageNode]


Node = Annotated[uuid.UUID, Query(description="A source or DW column, a DW table or a KPI.")]


@router.get("/lineage", operation_id="getLineage")
def get_lineage(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    lineage: LineageServiceDep,
    node: Node,
    direction: Direction = "both",
    depth: Annotated[int, Query(ge=1, le=MAX_DEPTH)] = MAX_DEPTH,
) -> NodeLineage:
    """The lineage graph around a node (any member): every node and edge on a path of at
    most `depth` edges to it (`upstream`), from it (`downstream`) or both. `uses` and
    `lookup` edges point at the table they steer."""
    return NodeLineage.model_validate(lineage.graph(user, workspace_id, node, direction, depth))


@router.get("/impact", operation_id="getLineageImpact")
def get_impact(
    workspace_id: uuid.UUID, user: CurrentUser, lineage: LineageServiceDep, node: Node
) -> ImpactReport:
    """What depends on a node, typically a source column (any member): the DW columns,
    DW tables and KPIs downstream of it."""
    return ImpactReport.model_validate(lineage.impact(user, workspace_id, node))
