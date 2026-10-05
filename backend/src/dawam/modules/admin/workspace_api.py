"""``/api/v1/admin/workspaces``: every Workspace's metadata and ownership rescue, for
admins only (spec stories 21, 22). Never a Workspace's content."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import AuthService, SecurityEventRecorder, User
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import Action, WorkspaceAdministration
from dawam.modules.workspaces import AdminWorkspace as AdminWorkspaceView
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit
from dawam.platform.request_context import client_ip

from .internal.access import allowed_to

router = APIRouter(tags=["admin"])

WorkspaceLister = Annotated[User, Depends(allowed_to(Action.LIST_ALL_WORKSPACES))]


def workspace_administration(request: Request) -> WorkspaceAdministration:
    state = request.app.state
    clock = state.services.clock
    return WorkspaceAdministration(
        state.engine,
        clock=clock,
        auth=AuthService(state.engine, state.settings, clock=clock),
        events=SecurityEventRecorder(state.engine, clock=clock),
        notifications=NotificationService(state.engine, clock=clock),
    )


WorkspaceAdministrationDep = Annotated[WorkspaceAdministration, Depends(workspace_administration)]


class WorkspaceOwner(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    display_name: str
    is_active: bool = Field(description="False for a deactivated owner.")


class AdminWorkspace(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: Literal["active", "archived"] = Field(description="`active` or `archived`.")
    owners: list[WorkspaceOwner]
    member_count: int
    has_active_owner: bool = Field(
        description="False when every owner is deactivated: ownership can be reassigned."
    )
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class AdminWorkspacePage(BaseModel):
    items: list[AdminWorkspace]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class ReassignOwnerRequest(BaseModel):
    user_id: uuid.UUID = Field(description="The active user who becomes an owner.")


def _out(workspace: AdminWorkspaceView) -> AdminWorkspace:
    return AdminWorkspace.model_validate(workspace)


@router.get("/admin/workspaces", operation_id="listAdminWorkspaces")
def list_admin_workspaces(
    admin: WorkspaceLister,
    workspaces: WorkspaceAdministrationDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> AdminWorkspacePage:
    """Every Workspace by name: owners, member count and dates, never its content
    (admins only)."""
    page = workspaces.list_all(admin, limit=limit, cursor=cursor)
    return AdminWorkspacePage(items=[_out(w) for w in page.items], next_cursor=page.next_cursor)


@router.post("/admin/workspaces/{workspace_id}/reassign-owner", operation_id="reassignOwner")
def reassign_owner(
    workspace_id: uuid.UUID,
    body: ReassignOwnerRequest,
    admin: WorkspaceLister,
    workspaces: WorkspaceAdministrationDep,
    request: Request,
) -> AdminWorkspace:
    """Make an active user an owner of a Workspace whose owners are all deactivated
    (admins only). 409 `has_active_owner` otherwise; 422 `user_inactive`. Recorded as a
    security event, and every member is notified."""
    return _out(workspaces.reassign_owner(admin, workspace_id, body.user_id, ip=client_ip(request)))
