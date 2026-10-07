"""``/api/v1/workspaces/{id}/data-warehouse/ddl``: the DDL package as a SQL download.

Handlers only translate HTTP to ``DdlService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .ddl_service import SQL_MIME, DdlService
from .model_service import ModelService
from .service import DataWarehouseService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse", tags=["data-warehouse"])

DdlLayer = Annotated[
    Literal["staging", "core", "mart"] | None,
    Query(description="One Layer's package; omit for the whole Data Warehouse."),
]


def ddl_service(request: Request) -> DdlService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return DdlService(
        DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        ModelService(state.engine, workspaces=workspaces, clock=clock),
    )


DdlServiceDep = Annotated[DdlService, Depends(ddl_service)]


@router.get(
    "/ddl",
    operation_id="downloadDdl",
    response_class=Response,
    responses={200: {"content": {SQL_MIME: {}}, "description": "The DDL package."}},
)
def download_ddl(
    workspace_id: uuid.UUID, user: CurrentUser, ddl: DdlServiceDep, layer: DdlLayer = None
) -> Response:
    """The DW Schema's DDL in the target platform's dialect (any member): each Layer's
    physical schema, its tables, foreign keys and one unknown-member `INSERT` per
    dimension. `layer` limits it to one Layer. 404 before the Data Warehouse is set up.
    To keep a copy, save it to the file area with `POST .../files/ddl`."""
    package = ddl.export(user, workspace_id, layer=layer)
    return Response(
        package.data,
        media_type=package.mime,
        headers={"Content-Disposition": f'attachment; filename="{package.name}"'},
    )
