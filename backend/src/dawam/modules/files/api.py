"""Files in a Workspace: the file areas of Source Systems and the Data Warehouse,
uploads, in-browser editing, downloads and zips.

Handlers only translate HTTP to ``FileService`` calls; the service authorizes every
call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.sources import SourceSystemService
from dawam.modules.warehouse import DataWarehouseService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .search import DocumentSearchService, Passage
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
        warehouses=DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        storage=state.storage,
        search=state.document_search,
        clock=clock,
        max_upload_bytes=state.settings.upload_max_bytes,
    )


FileServiceDep = Annotated[FileService, Depends(file_service)]


def document_search(request: Request) -> DocumentSearchService:
    return request.app.state.document_search


DocumentSearchDep = Annotated[DocumentSearchService, Depends(document_search)]


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


def _zip_response(content) -> Response:
    return Response(
        content.data,
        media_type=content.mime,
        headers={
            "Content-Disposition": _attachment(content.name),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


_ZIP_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"application/zip": {}}, "description": "The area's files as a zip."}
}


@router.get(
    "/systems/{system_id}/files/download",
    operation_id="downloadSourceSystemFiles",
    response_class=Response,
    responses=_ZIP_RESPONSES,
)
def download_source_system_files(
    workspace_id: uuid.UUID, system_id: uuid.UUID, user: CurrentUser, files: FileServiceDep
) -> Response:
    """The whole file area of a Source System as a zip (any member)."""
    return _zip_response(files.zip_system_area(user, workspace_id, system_id))


@router.get("/data-warehouse/files", operation_id="listDataWarehouseFiles")
def list_data_warehouse_files(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    files: FileServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> WorkspaceFilePage:
    """The files of the Data Warehouse's file area (any member), by name. 404 until the
    Data Warehouse is set up."""
    page = files.list_for_warehouse(user, workspace_id, limit=limit, cursor=cursor)
    return WorkspaceFilePage(items=[_out(f) for f in page.items], next_cursor=page.next_cursor)


@router.post("/data-warehouse/files", operation_id="uploadDataWarehouseFile", status_code=201)
def upload_data_warehouse_file(
    workspace_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="The file to upload.")],
    user: CurrentUser,
    files: FileServiceDep,
) -> WorkspaceFile:
    """Upload a file to the Data Warehouse's file area (owners and editors), as multipart
    form data; same types and limits as a Source System upload."""
    return _out(
        files.upload_to_warehouse(
            user, workspace_id, filename=file.filename or "", content=file.file
        )
    )


@router.get(
    "/data-warehouse/files/download",
    operation_id="downloadDataWarehouseFiles",
    response_class=Response,
    responses=_ZIP_RESPONSES,
)
def download_data_warehouse_files(
    workspace_id: uuid.UUID, user: CurrentUser, files: FileServiceDep
) -> Response:
    """The whole file area of the Data Warehouse as a zip (any member)."""
    return _zip_response(files.zip_warehouse_area(user, workspace_id))


class TextContent(BaseModel):
    file: WorkspaceFile
    content: str = Field(description="The file's text (UTF-8).")


class TextUpdate(BaseModel):
    content: str = Field(description="The new text; it overwrites the file in place.")


@router.get("/files/{file_id}/content", operation_id="readFileText")
def read_file_text(
    workspace_id: uuid.UUID, file_id: uuid.UUID, user: CurrentUser, files: FileServiceDep
) -> TextContent:
    """A text file's content for editing (any member). 415 `not_editable` for a binary
    file (PDF, DOCX, XLSX, images)."""
    text = files.read_text(user, workspace_id, file_id)
    return TextContent(file=_out(text.file), content=text.content)


@router.put("/files/{file_id}/content", operation_id="saveFileText")
def save_file_text(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    body: TextUpdate,
    user: CurrentUser,
    files: FileServiceDep,
) -> WorkspaceFile:
    """Overwrite a Markdown, SQL, YAML, CSV, JSON or text file in place (owners and
    editors); there is no version history. 413 `file_too_large`, 415 `not_editable`."""
    return _out(files.write_text(user, workspace_id, file_id, content=body.content))


@router.put("/files/{file_id}/replace", operation_id="replaceFile")
def replace_file(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="The new content, of the same type.")],
    user: CurrentUser,
    files: FileServiceDep,
) -> WorkspaceFile:
    """Replace a file's content with an upload (owners and editors), keeping its name; the
    upload's own name is ignored but its content must be of the type the file's name says.
    413 `file_too_large`, 415 `unsupported_file_type`."""
    return _out(files.replace(user, workspace_id, file_id, content=file.file))


class DocumentPassage(BaseModel):
    file_id: uuid.UUID
    document: str = Field(description="The document's name: the citation's first half.")
    section: str = Field(description="The heading (or `Part N`) the passage sits under.")
    text: str
    score: float = Field(description="Relative rank: higher is a better match.")
    source_system_id: uuid.UUID


class DocumentSearchResults(BaseModel):
    items: list[DocumentPassage]


class ReindexStarted(BaseModel):
    job_id: uuid.UUID = Field(
        description="The `reindex_documents` job: follow it at `GET /jobs/{job_id}`."
    )
    status: str


def _passage(passage: Passage) -> DocumentPassage:
    return DocumentPassage(
        file_id=passage.file_id,
        document=passage.document,
        section=passage.section,
        text=passage.text,
        score=passage.score,
        source_system_id=passage.source_system_id,
    )


@router.get("/documents/search", operation_id="searchDocuments")
def search_documents(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    search: DocumentSearchDep,
    q: Annotated[str, Query(max_length=500, description="What to look for.")] = "",
    system_id: Annotated[uuid.UUID | None, Query(description="Only this Source System.")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DocumentSearchResults:
    """Search the Workspace's uploaded documents (any member). Full-text search with Arabic
    normalisation, combined with vector search when the Workspace's data-sharing level
    includes documents and its internal-only setting allows the embedding provider. Each
    result is a cited passage: the document and its section."""
    passages = search.search(user, workspace_id, q, system_id=system_id, limit=limit)
    return DocumentSearchResults(items=[_passage(p) for p in passages])


@router.post("/documents/reindex", operation_id="reindexDocuments", status_code=202)
def reindex_documents(
    workspace_id: uuid.UUID, user: CurrentUser, search: DocumentSearchDep
) -> ReindexStarted:
    """Index every document of the Workspace again (owners and editors), as a background
    job: run it after the embedding model or its dimension changes."""
    job = search.start_reindex(user, workspace_id)
    return ReindexStarted(job_id=job.id, status=job.status)
