"""``/api/v1/workspaces/{workspace_id}/systems``: a Workspace's Source Systems.

Handlers only translate HTTP to ``SourceSystemService`` calls; the service authorizes
every call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .connection_api import router as connection_router
from .service import SourceSystem as SourceSystemView
from .service import SourceSystemService
from .tables import CODE_MAX_LENGTH, DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, OWNER_MAX_LENGTH

router = APIRouter(prefix="/workspaces/{workspace_id}/systems", tags=["sources"])


def source_system_service(request: Request) -> SourceSystemService:
    state = request.app.state
    clock = state.services.clock
    return SourceSystemService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


SourceSystemServiceDep = Annotated[SourceSystemService, Depends(source_system_service)]

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]
# Not length-bound here: the service answers `invalid_system_code`, naming the rule.
Code = Annotated[str, Field(max_length=CODE_MAX_LENGTH * 4)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH)
]
Owner = Annotated[str, StringConstraints(strip_whitespace=True, max_length=OWNER_MAX_LENGTH)]


class SourceSystem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    code: str = Field(
        description="The System Code: identifier-safe, unique in the Workspace (e.g. `cbs`)."
    )
    description: str
    business_owner: str
    technical_owner: str
    version: int = Field(description="Send it back when editing; a stale one gets 409.")
    created_at: datetime
    updated_at: datetime


class SourceSystemPage(BaseModel):
    items: list[SourceSystem]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class CreateSourceSystemRequest(BaseModel):
    name: Name
    code: Code
    description: Description = ""
    business_owner: Owner = ""
    technical_owner: Owner = ""


class UpdateSourceSystemRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    name: Name | None = None
    code: Code | None = Field(
        default=None, description="A different System Code (owners only; 403 for editors)."
    )
    description: Description | None = None
    business_owner: Owner | None = None
    technical_owner: Owner | None = None


def _out(system: SourceSystemView) -> SourceSystem:
    return SourceSystem.model_validate(system)


@router.get("", operation_id="listSourceSystems")
def list_source_systems(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    systems: SourceSystemServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> SourceSystemPage:
    """The Workspace's Source Systems (any member), ordered by System Code."""
    page = systems.list(user, workspace_id, limit=limit, cursor=cursor)
    return SourceSystemPage(items=[_out(s) for s in page.items], next_cursor=page.next_cursor)


@router.post("", operation_id="createSourceSystem", status_code=201)
def create_source_system(
    workspace_id: uuid.UUID,
    body: CreateSourceSystemRequest,
    user: CurrentUser,
    systems: SourceSystemServiceDep,
) -> SourceSystem:
    """Add a Source System (owners and editors). 422 `invalid_system_code` if the System
    Code is not identifier-safe; 409 `system_code_taken` if the Workspace has it already."""
    return _out(
        systems.create(
            user,
            workspace_id,
            name=body.name,
            code=body.code,
            description=body.description,
            business_owner=body.business_owner,
            technical_owner=body.technical_owner,
        )
    )


@router.get("/{system_id}", operation_id="getSourceSystem")
def get_source_system(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    systems: SourceSystemServiceDep,
) -> SourceSystem:
    """Open a Source System (any member)."""
    return _out(systems.get(user, workspace_id, system_id))


@router.patch("/{system_id}", operation_id="updateSourceSystem")
def update_source_system(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: UpdateSourceSystemRequest,
    user: CurrentUser,
    systems: SourceSystemServiceDep,
) -> SourceSystem:
    """Edit a Source System (owners and editors); fields left out stay as they are.
    Changing the System Code is for owners only (403 for editors). Until staging
    exists, a code change applies directly."""
    return _out(
        systems.update(
            user,
            workspace_id,
            system_id,
            version=body.version,
            name=body.name,
            code=body.code,
            description=body.description,
            business_owner=body.business_owner,
            technical_owner=body.technical_owner,
        )
    )


# Mounted last: the OpenAPI document lists the Connection routes after the system routes.
router.include_router(connection_router, prefix="/{system_id}/connection")
