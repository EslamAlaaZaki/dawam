"""``GET /workspaces/{id}/pii/inventory/export``: the PII inventory as XLSX or Markdown
(story 140).

Handlers only translate HTTP to ``PiiInventoryService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from dawam.modules.auth import AuthService, CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .pii_inventory import MARKDOWN_MIME, XLSX_MIME, PiiInventoryService
from .service import LineageService

router = APIRouter(prefix="/workspaces/{workspace_id}/pii", tags=["lineage"])

InventoryFormatParam = Annotated[
    Literal["xlsx", "md"],
    Query(description="`xlsx` (three sheets) or `md` (Markdown tables)."),
]


def pii_inventory_service(request: Request) -> PiiInventoryService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return PiiInventoryService(
        state.engine,
        LineageService(state.engine, workspaces=workspaces),
        auth=AuthService(state.engine, state.settings, clock=clock),
        clock=clock,
    )


PiiInventoryServiceDep = Annotated[PiiInventoryService, Depends(pii_inventory_service)]


@router.get(
    "/inventory/export",
    operation_id="downloadPiiInventory",
    response_class=Response,
    responses={
        200: {
            "content": {XLSX_MIME: {}, MARKDOWN_MIME: {}},
            "description": "The PII inventory.",
        }
    },
)
def download_pii_inventory(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    inventories: PiiInventoryServiceDep,
    format: InventoryFormatParam = "xlsx",
) -> Response:
    """The PII inventory (any member): the confirmed PII source columns and the DW columns
    they feed, with category, hashing (`pseudonymised` or `not hashed`), handling note and
    reviewer, never values. PII-influenced tables are listed on their own sheet or section.
    To keep a copy, save it to the file area with
    `POST .../data-warehouse/files/pii-inventory`."""
    inventory = inventories.export(user, workspace_id, fmt=format)
    return Response(
        inventory.data,
        media_type=inventory.mime,
        headers={"Content-Disposition": f'attachment; filename="{inventory.name}"'},
    )
