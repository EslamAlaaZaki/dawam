"""``/api/v1/workspaces/{id}/documentation-pack``: the documentation pack as zipped Markdown
or one HTML file, and its Markdown copy in the Data Warehouse's file area (story 128).

Handlers only translate HTTP to ``DocumentationPackService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.files import FileService
from dawam.modules.kpis import KpiService
from dawam.modules.sources import SnapshotService, SourceSystemService
from dawam.modules.warehouse import DataWarehouseService, MappingService, ModelService
from dawam.modules.workspaces import WorkspaceService

from .service import HTML_MIME, ZIP_MIME, DocumentationPackService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["documentation-pack"])

PackFormat = Annotated[
    Literal["zip", "html"],
    Query(description="`zip` (Markdown files) or `html` (a single self-contained file)."),
]


def documentation_pack_service(request: Request) -> DocumentationPackService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    systems = SourceSystemService(state.engine, workspaces=workspaces, clock=clock)
    return DocumentationPackService(
        workspaces=workspaces,
        systems=systems,
        snapshots=SnapshotService(
            state.engine,
            workspaces=workspaces,
            jobs=state.services.jobs,
            encryption_key=state.settings.encryption_key.get_secret_value(),
            clock=clock,
        ),
        warehouses=DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        model=ModelService(state.engine, workspaces=workspaces, clock=clock),
        mappings=MappingService(state.engine, workspaces=workspaces, clock=clock),
        kpis=KpiService(state.engine, workspaces=workspaces, systems=systems, clock=clock),
    )


DocumentationPackServiceDep = Annotated[
    DocumentationPackService, Depends(documentation_pack_service)
]


def file_service(request: Request) -> FileService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return FileService(
        state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(state.engine, workspaces=workspaces, clock=clock),
        warehouses=DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        storage=state.storage,
        search=state.document_search,
        clock=clock,
        max_upload_bytes=state.settings.upload_max_bytes,
    )


FileServiceDep = Annotated[FileService, Depends(file_service)]


class SavedPack(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    size: int = Field(description="Bytes.")


@router.get(
    "/documentation-pack",
    operation_id="downloadDocumentationPack",
    response_class=Response,
    responses={
        200: {
            "content": {ZIP_MIME: {}, HTML_MIME: {}},
            "description": "The documentation pack.",
        }
    },
)
def download_documentation_pack(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    packs: DocumentationPackServiceDep,
    format: PackFormat = "zip",
) -> Response:
    """The documentation pack (any member, also in an archived Workspace): Source Schemas,
    the DW Schema per Layer, mappings, KPIs and a lineage summary. `zip` holds one Markdown
    page per Source System, Layer and topic with a `README.md`; `html` is the same content in
    one self-contained file. Metadata only, never row values. To keep a copy, save it to
    the file area with `POST .../data-warehouse/files/documentation-pack`."""
    pack = packs.zip(user, workspace_id) if format == "zip" else packs.html(user, workspace_id)
    return Response(
        pack.data,
        media_type=pack.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{pack.name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/data-warehouse/files/documentation-pack",
    operation_id="saveDocumentationPack",
    status_code=201,
)
def save_documentation_pack(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    files: FileServiceDep,
    packs: DocumentationPackServiceDep,
) -> SavedPack:
    """Save the documentation pack into the Data Warehouse's file area as one Markdown file,
    `documentation-pack.md` (owners and editors), overwriting the previous one. The file
    area holds no HTML or zip, so download those. 404 before the Data Warehouse is set up;
    refused in an archived Workspace."""
    pack = packs.combined_markdown(user, workspace_id)
    saved = files.save_generated_to_warehouse(user, workspace_id, name=pack.name, data=pack.data)
    return SavedPack.model_validate(saved)
