"""``/api/v1/workspaces``: create, list, open and edit Workspaces.

Handlers only translate HTTP to ``WorkspaceService`` calls; the service authorizes
every call through the policy (``can``), so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .internal.policy import Action, WorkspaceRole
from .service import Layer, StageStatus, WorkspaceService
from .service import Workspace as WorkspaceView
from .tables import DESCRIPTION_MAX_LENGTH, DOMAIN_MAX_LENGTH, NAME_MAX_LENGTH

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def workspace_service(request: Request) -> WorkspaceService:
    state = request.app.state
    return WorkspaceService(state.engine, clock=state.services.clock)


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(workspace_service)]

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH)
]
Domain = Annotated[str, StringConstraints(strip_whitespace=True, max_length=DOMAIN_MAX_LENGTH)]


class Workspace(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    domain: str = Field(description="The business domain, e.g. “Retail banking”.")
    role: WorkspaceRole = Field(description="Your role in this Workspace.")
    permissions: list[Action] = Field(
        description="The Workspace actions you may perform. The UI uses it to show or hide "
        "controls; the server checks every request regardless."
    )
    version: int = Field(description="Send it back when editing; a stale one gets 409.")
    created_at: datetime
    updated_at: datetime


class WorkspacePage(BaseModel):
    items: list[Workspace]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class CreateWorkspaceRequest(BaseModel):
    name: Name
    description: Description = ""
    domain: Domain = ""


class UpdateWorkspaceRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    name: Name | None = None
    description: Description | None = None
    domain: Domain | None = None


class SystemProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    system_id: uuid.UUID
    name: str
    status: StageStatus


class KpiProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: StageStatus


class LayerProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer: Layer
    status: StageStatus


class StageProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_analysis: list[SystemProgress] = Field(
        description="Source Analysis, one entry per Source System (none until one is added)."
    )
    kpis: KpiProgress
    dw_modeling: list[LayerProgress] = Field(description="DW Modeling, one entry per Layer.")


def _out(workspace: WorkspaceView) -> Workspace:
    return Workspace(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        domain=workspace.domain,
        role=workspace.role,
        permissions=sorted(workspace.permissions),
        version=workspace.version,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


@router.get("", operation_id="listWorkspaces")
def list_workspaces(
    user: CurrentUser,
    workspaces: WorkspaceServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> WorkspacePage:
    """The Workspaces you are a member of, with your role in each, ordered by name."""
    page = workspaces.list_for(user, limit=limit, cursor=cursor)
    return WorkspacePage(items=[_out(w) for w in page.items], next_cursor=page.next_cursor)


@router.post("", operation_id="createWorkspace", status_code=201)
def create_workspace(
    body: CreateWorkspaceRequest, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> Workspace:
    """Create a Workspace; you become its owner."""
    return _out(
        workspaces.create(user, name=body.name, description=body.description, domain=body.domain)
    )


@router.get("/{workspace_id}", operation_id="getWorkspace")
def get_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> Workspace:
    """Open a Workspace you are a member of (404 for anyone else, admins included)."""
    return _out(workspaces.get(user, workspace_id))


@router.patch("/{workspace_id}", operation_id="updateWorkspace")
def update_workspace(
    workspace_id: uuid.UUID,
    body: UpdateWorkspaceRequest,
    user: CurrentUser,
    workspaces: WorkspaceServiceDep,
) -> Workspace:
    """Edit the Workspace's details (owners only). Fields left out stay as they are."""
    return _out(
        workspaces.update(
            user,
            workspace_id,
            version=body.version,
            name=body.name,
            description=body.description,
            domain=body.domain,
        )
    )


@router.get("/{workspace_id}/progress", operation_id="getStageProgress")
def get_stage_progress(
    workspace_id: uuid.UUID, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> StageProgress:
    """Where the Workspace's work stands: Source Analysis per Source System, KPIs and
    DW Modeling per Layer. Every member sees the same."""
    return StageProgress.model_validate(workspaces.stage_progress(user, workspace_id))
