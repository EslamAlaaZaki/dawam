"""External links of Source Systems and document-to-object links (stories 65, 66).

Handlers only translate HTTP to ``LinkService`` calls; the service authorizes every
call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .api import WorkspaceFile, WorkspaceFilePage
from .link_service import FileObjectLink as FileObjectLinkView
from .link_service import LinkService
from .link_service import SourceLink as SourceLinkView
from .tables import NOTE_MAX_LENGTH, TITLE_MAX_LENGTH, URL_MAX_LENGTH

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["files"])


def link_service(request: Request) -> LinkService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return LinkService(
        state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(state.engine, workspaces=workspaces, clock=clock),
        clock=clock,
    )


LinkServiceDep = Annotated[LinkService, Depends(link_service)]

LinkKind = Literal["repo", "jira", "confluence", "other"]
ObjectType = Literal["table", "column"]

# Looser than the table's limits: the service answers `invalid_source_link`, naming the field.
Title = Annotated[str, Field(max_length=TITLE_MAX_LENGTH * 2)]
Url = Annotated[str, Field(max_length=URL_MAX_LENGTH * 2)]
Note = Annotated[str, StringConstraints(max_length=NOTE_MAX_LENGTH * 2)]


class SourceLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_system_id: uuid.UUID
    kind: LinkKind
    title: str
    url: str = Field(description="An absolute `http` or `https` URL.")
    note: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class SourceLinkPage(BaseModel):
    items: list[SourceLink]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class CreateSourceLinkRequest(BaseModel):
    kind: LinkKind
    title: Title
    url: Url
    note: Note = ""


class UpdateSourceLinkRequest(BaseModel):
    kind: LinkKind | None = None
    title: Title | None = None
    url: Url | None = None
    note: Note | None = None


class FileObjectLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: uuid.UUID
    object_type: ObjectType
    object_id: uuid.UUID = Field(description="The Source Table or Column.")
    created_at: datetime


class FileObjectLinkList(BaseModel):
    items: list[FileObjectLink]


class LinkDocumentRequest(BaseModel):
    object_type: ObjectType
    object_id: uuid.UUID = Field(description="The Source Table or Column to link to.")


def _link(link: SourceLinkView) -> SourceLink:
    return SourceLink.model_validate(link)


def _object_link(link: FileObjectLinkView) -> FileObjectLink:
    return FileObjectLink.model_validate(link)


@router.get("/systems/{system_id}/links", operation_id="listSourceLinks")
def list_source_links(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    links: LinkServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> SourceLinkPage:
    """A Source System's links to repositories, Jira issues and Confluence pages (any
    member), by title."""
    page = links.list_links(user, workspace_id, system_id, limit=limit, cursor=cursor)
    return SourceLinkPage(items=[_link(i) for i in page.items], next_cursor=page.next_cursor)


@router.post("/systems/{system_id}/links", operation_id="createSourceLink", status_code=201)
def create_source_link(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: CreateSourceLinkRequest,
    user: CurrentUser,
    links: LinkServiceDep,
) -> SourceLink:
    """Link a page to a Source System (owners and editors). Only `http` and `https`
    URLs; 422 `invalid_source_link` names the field that is wrong."""
    return _link(
        links.create_link(
            user,
            workspace_id,
            system_id,
            kind=body.kind,
            title=body.title,
            url=body.url,
            note=body.note,
        )
    )


@router.patch("/systems/{system_id}/links/{source_link_id}", operation_id="updateSourceLink")
def update_source_link(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    source_link_id: uuid.UUID,
    body: UpdateSourceLinkRequest,
    user: CurrentUser,
    links: LinkServiceDep,
) -> SourceLink:
    """Edit a link (owners and editors); fields left out stay as they are."""
    return _link(
        links.update_link(
            user,
            workspace_id,
            system_id,
            source_link_id,
            kind=body.kind,
            title=body.title,
            url=body.url,
            note=body.note,
        )
    )


@router.delete(
    "/systems/{system_id}/links/{source_link_id}",
    operation_id="deleteSourceLink",
    status_code=204,
)
def delete_source_link(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    source_link_id: uuid.UUID,
    user: CurrentUser,
    links: LinkServiceDep,
) -> Response:
    """Remove a link (owners and editors)."""
    links.delete_link(user, workspace_id, system_id, source_link_id)
    return Response(status_code=204)


@router.get("/files/{file_id}/object-links", operation_id="listFileObjectLinks")
def list_file_object_links(
    workspace_id: uuid.UUID, file_id: uuid.UUID, user: CurrentUser, links: LinkServiceDep
) -> FileObjectLinkList:
    """The tables and columns a document is linked to (any member)."""
    found = links.links_of_document(user, workspace_id, file_id)
    return FileObjectLinkList(items=[_object_link(i) for i in found])


@router.post(
    "/files/{file_id}/object-links",
    operation_id="linkFileToObject",
    status_code=201,
    responses={200: {"description": "The file was already linked to that object."}},
)
def link_file_to_object(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    body: LinkDocumentRequest,
    response: Response,
    user: CurrentUser,
    links: LinkServiceDep,
) -> FileObjectLink:
    """Link a document to a table or column (owners and editors), so it shows on that
    page. Linking again changes nothing (200)."""
    link, created = links.link_document(
        user, workspace_id, file_id, body.object_type, body.object_id
    )
    if not created:
        response.status_code = 200
    return _object_link(link)


@router.delete(
    "/files/{file_id}/object-links/{object_type}/{object_id}",
    operation_id="unlinkFileFromObject",
    status_code=204,
)
def unlink_file_from_object(
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    object_type: ObjectType,
    object_id: uuid.UUID,
    user: CurrentUser,
    links: LinkServiceDep,
) -> Response:
    """Remove a document's link to a table or column (owners and editors)."""
    links.unlink_document(user, workspace_id, file_id, object_type, object_id)
    return Response(status_code=204)


@router.get("/objects/{object_type}/{object_id}/documents", operation_id="listDocumentsOfObject")
def list_documents_of_object(
    workspace_id: uuid.UUID,
    object_type: ObjectType,
    object_id: uuid.UUID,
    user: CurrentUser,
    links: LinkServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> WorkspaceFilePage:
    """The documents linked to a table or column (any member), by name."""
    page = links.documents_of_object(
        user, workspace_id, object_type, object_id, limit=limit, cursor=cursor
    )
    return WorkspaceFilePage(
        items=[WorkspaceFile.model_validate(f) for f in page.items], next_cursor=page.next_cursor
    )
