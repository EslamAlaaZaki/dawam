"""Files in a Workspace: uploads to a Source System's file area, and downloads.

Handlers only translate HTTP to ``FileService`` calls; the service authorizes every
call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .service import FileService
from .service import WorkspaceFile as WorkspaceFileView

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["files"])


def file_service(request: Request) -> FileService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return FileService(
        state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(state.engine, workspaces=workspaces, clock=clock),
        storage=state.storage,
        clock=clock,
        max_upload_bytes=state.settings.upload_max_bytes,
    )


FileServiceDep = Annotated[FileService, Depends(file_service)]


class WorkspaceFile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    owner_kind: str = Field(description="`source_system` or `data_warehouse`.")
    owner_id: uuid.UUID
    name: str
    kind: str = Field(description="`uploaded` or `generated`.")
    mime: str = Field(description="The type found in the content, not the one the client sent.")
    size: int = Field(description="Bytes.")
    text_status: str = Field(
        description="`extracted`, `no_text_found` (e.g. a scanned PDF) or `none` (an image)."
    )
    updated_by: uuid.UUID | None
    updated_at: datetime


class WorkspaceFilePage(BaseModel):
    items: list[WorkspaceFile]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


def _out(file: WorkspaceFileView) -> WorkspaceFile:
    return WorkspaceFile.model_validate(file)


@router.get("/systems/{system_id}/files", operation_id="listSourceSystemFiles")
def list_source_system_files(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    files: FileServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> WorkspaceFilePage:
    """The files and documents of a Source System's file area (any member), by name."""
    page = files.list_for_system(user, workspace_id, system_id, limit=limit, cursor=cursor)
    return WorkspaceFilePage(items=[_out(f) for f in page.items], next_cursor=page.next_cursor)


@router.post("/systems/{system_id}/files", operation_id="uploadSourceSystemFile", status_code=201)
def upload_source_system_file(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="The document to upload.")],
    user: CurrentUser,
    files: FileServiceDep,
) -> WorkspaceFile:
    """Upload a document to a Source System (owners and editors), as multipart form data.
    PDF, DOCX, XLSX, Markdown, text and PNG/JPEG/GIF/WebP images up to the size limit
    (25 MB by default); the type is checked against the content. A file with the same
    name is overwritten. 413 `file_too_large`, 415 `unsupported_file_type`,
    422 `invalid_file_name`."""
    return _out(
        files.upload(user, workspace_id, system_id, filename=file.filename or "", content=file.file)
    )


def _attachment(name: str) -> str:
    """A ``Content-Disposition: attachment`` value for ``name`` (RFC 6266 / 5987)."""
    fallback = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\%' else "_" for c in name
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.get(
    "/files/{file_id}/download",
    operation_id="downloadFile",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The file."}},
)
def download_file(
    workspace_id: uuid.UUID, file_id: uuid.UUID, user: CurrentUser, files: FileServiceDep
) -> Response:
    """A file's content (any member), always as an attachment, never shown inline."""
    content = files.download(user, workspace_id, file_id)
    return Response(
        content.data,
        media_type=content.mime,
        headers={
            "Content-Disposition": _attachment(content.name),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
