"""``/api/v1/workspaces/{id}/data-warehouse/mapping-sheet``: the mapping sheet as XLSX or CSV
(story 108).

Handlers only translate HTTP to ``MappingExportService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .mapping_export import CSV_MIME, XLSX_MIME, MappingExportService
from .mapping_service import MappingService
from .model_service import ModelService
from .service import DataWarehouseService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse", tags=["data-warehouse"])

SheetFormat = Annotated[
    Literal["xlsx", "csv"],
    Query(description="`xlsx` (a Mapping and a Branches sheet) or `csv` (the Mapping sheet)."),
]
SheetLayer = Annotated[
    Literal["core", "mart"] | None,
    Query(description="One Layer's mappings; omit for Core and Mart."),
]


def mapping_export_service(request: Request) -> MappingExportService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return MappingExportService(
        state.engine,
        DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        ModelService(state.engine, workspaces=workspaces, clock=clock),
        MappingService(state.engine, workspaces=workspaces, clock=clock),
    )


MappingExportServiceDep = Annotated[MappingExportService, Depends(mapping_export_service)]


@router.get(
    "/mapping-sheet",
    operation_id="downloadMappingSheet",
    response_class=Response,
    responses={
        200: {
            "content": {XLSX_MIME: {}, CSV_MIME: {}},
            "description": "The mapping sheet.",
        }
    },
)
def download_mapping_sheet(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    exports: MappingExportServiceDep,
    format: SheetFormat = "xlsx",
    layer: SheetLayer = None,
) -> Response:
    """The Core and Mart mappings as a mapping sheet (any member): one row per target column
    (per branch) with its type, inputs as `table.column` separated by `;`, rule, SQL,
    mapping type and lookup dimension. XLSX adds a `Branches` sheet with each branch's join
    path, filters, group-by, integration rule and match keys; CSV holds the first sheet.
    404 before the Data Warehouse is set up. To keep a copy, save it to the file area with
    `POST .../files/mapping-sheet`."""
    sheet = exports.export(user, workspace_id, fmt=format, layer=layer)
    return Response(
        sheet.data,
        media_type=sheet.mime,
        headers={"Content-Disposition": f'attachment; filename="{sheet.name}"'},
    )
