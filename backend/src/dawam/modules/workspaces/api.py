"""``/api/v1/workspaces``: create, list, open and edit Workspaces, and their members.

Handlers only translate HTTP to ``WorkspaceService`` calls; the service authorizes
every call through the policy (``can``), so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.activity import ActivityService
from dawam.modules.audit import AuditService
from dawam.modules.auth import (
    MAX_EMAIL_LENGTH,
    AuthService,
    CurrentUser,
    Invitations,
    SecurityEventRecorder,
)
from dawam.platform.email import Delivery
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit
from dawam.platform.request_context import client_ip

from .internal.members import Member as MemberView
from .internal.members import MemberAdded, MembershipService
from .internal.policy import Action, WorkspaceRole, can
from .service import Layer, StageStatus, WorkspaceService, WorkspaceStatus
from .service import Workspace as WorkspaceView
from .tables import DESCRIPTION_MAX_LENGTH, DOMAIN_MAX_LENGTH, NAME_MAX_LENGTH

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def workspace_service(request: Request) -> WorkspaceService:
    state = request.app.state
    clock = state.services.clock
    return WorkspaceService(
        state.engine,
        clock=clock,
        events=SecurityEventRecorder(state.engine, clock=clock),
        on_archived=getattr(state, "on_workspace_archived", None),
        source_analysis=getattr(state, "source_analysis", None),
        on_created=getattr(state, "on_workspace_created", None),
    )


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(workspace_service)]


def membership_service(request: Request) -> MembershipService:
    state = request.app.state
    clock = state.services.clock
    return MembershipService(
        state.engine,
        clock=clock,
        auth=AuthService(state.engine, state.settings, clock=clock),
        invitations=Invitations(state.engine, state.settings, mailer=state.mailer, clock=clock),
        registration=getattr(state, "registration_policy", None),
    )


MembershipServiceDep = Annotated[MembershipService, Depends(membership_service)]


def activity_service(request: Request) -> ActivityService:
    state = request.app.state
    auth = AuthService(state.engine, state.settings, clock=state.services.clock)
    return ActivityService(state.engine, auth=auth)


ActivityServiceDep = Annotated[ActivityService, Depends(activity_service)]


def audit_service(request: Request) -> AuditService:
    return AuditService(request.app.state.engine)


AuditServiceDep = Annotated[AuditService, Depends(audit_service)]


def auth_service(request: Request) -> AuthService:
    state = request.app.state
    return AuthService(state.engine, state.settings, clock=state.services.clock)


AuthServiceDep = Annotated[AuthService, Depends(auth_service)]

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
    status: WorkspaceStatus = Field(
        description="`archived`: read-only until an owner or admin unarchives it."
    )
    archived_at: datetime | None
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


class DeleteWorkspaceRequest(BaseModel):
    name: str = Field(description="The Workspace's name, typed exactly, to confirm.")


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
        status=workspace.status,
        archived_at=workspace.archived_at,
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


@router.post(
    "/{workspace_id}/archive",
    operation_id="archiveWorkspace",
    status_code=204,
    response_class=Response,
)
def archive_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> Response:
    """Make the Workspace read-only (owners, and admins even if not members). Reads and
    exports keep working. 409 `workspace_archived` if it already is."""
    workspaces.archive(user, workspace_id)
    return Response(status_code=204)


@router.post(
    "/{workspace_id}/unarchive",
    operation_id="unarchiveWorkspace",
    status_code=204,
    response_class=Response,
)
def unarchive_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> Response:
    """Make an archived Workspace editable again (owners and admins). 409
    `workspace_not_archived` if it is not archived."""
    workspaces.unarchive(user, workspace_id)
    return Response(status_code=204)


@router.delete(
    "/{workspace_id}", operation_id="deleteWorkspace", status_code=204, response_class=Response
)
def delete_workspace(
    workspace_id: uuid.UUID,
    body: DeleteWorkspaceRequest,
    user: CurrentUser,
    workspaces: WorkspaceServiceDep,
    request: Request,
) -> Response:
    """Delete the Workspace for good (owners; admins who are not members only once it is
    archived: 409 `workspace_not_archived`). The body's `name` must be its name, else 422
    `name_mismatch`. Recorded as a security event."""
    workspaces.delete(user, workspace_id, name=body.name, ip=client_ip(request))
    return Response(status_code=204)


@router.get("/{workspace_id}/progress", operation_id="getStageProgress")
def get_stage_progress(
    workspace_id: uuid.UUID, user: CurrentUser, workspaces: WorkspaceServiceDep
) -> StageProgress:
    """Where the Workspace's work stands: Source Analysis per Source System, KPIs and
    DW Modeling per Layer. Every member sees the same."""
    return StageProgress.model_validate(workspaces.stage_progress(user, workspace_id))


class Member(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    display_name: str
    role: WorkspaceRole
    is_active: bool = Field(description="False for a deactivated user, who cannot sign in.")
    added_at: datetime


class MemberList(BaseModel):
    items: list[Member] = Field(description="Every member, by display name.")


class AddMemberRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    role: WorkspaceRole


class InvitationOut(BaseModel):
    email: str
    expires_at: datetime


class MemberAddedOut(BaseModel):
    outcome: Literal["added"] = "added"
    member: Member


class MemberInvitedOut(BaseModel):
    outcome: Literal["invited"] = "invited"
    invitation: InvitationOut
    delivery: Delivery = Field(
        description="`sent`: the invitation was emailed; `link_for_admin`: it could not be, "
        "so an admin has to share its link."
    )


class ChangeRoleRequest(BaseModel):
    role: WorkspaceRole


class TransferOwnershipRequest(BaseModel):
    user_id: uuid.UUID = Field(description="The member who becomes an owner.")


def _member_out(member: MemberView) -> Member:
    return Member.model_validate(member)


@router.get("/{workspace_id}/members", operation_id="listMembers")
def list_members(
    workspace_id: uuid.UUID, user: CurrentUser, members: MembershipServiceDep
) -> MemberList:
    """The Workspace's members and their roles (any member). Not paged, unlike other
    lists: a Workspace has a team's worth of members, and the list is sorted by display
    name, which lives with the users."""
    return MemberList(items=[_member_out(m) for m in members.list(user, workspace_id)])


@router.post("/{workspace_id}/members", operation_id="addMember", status_code=201)
def add_member(
    workspace_id: uuid.UUID,
    body: AddMemberRequest,
    user: CurrentUser,
    members: MembershipServiceDep,
    request: Request,
) -> Annotated[MemberAddedOut | MemberInvitedOut, Field(discriminator="outcome")]:
    """Add someone by email with a role (owners only). If no account has the email, they
    are invited into the Workspace instead (`outcome: invited`): only admins may invite,
    or anyone while self-registration is open to that email (403 `invite_not_allowed`
    otherwise). 409 `already_member` if they are a member."""
    result = members.add(
        user, workspace_id, email=body.email, role=body.role, ip=client_ip(request)
    )
    if isinstance(result, MemberAdded):
        return MemberAddedOut(member=_member_out(result.member))
    return MemberInvitedOut(
        invitation=InvitationOut(
            email=result.invitation.email, expires_at=result.invitation.expires_at
        ),
        delivery=result.delivery,
    )


@router.patch("/{workspace_id}/members/{member_id}", operation_id="changeMemberRole")
def change_member_role(
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    body: ChangeRoleRequest,
    user: CurrentUser,
    members: MembershipServiceDep,
) -> Member:
    """Change a member's role (owners only). 409 `last_owner` if it would leave the
    Workspace without an owner."""
    return _member_out(members.change_role(user, workspace_id, member_id, body.role))


@router.delete(
    "/{workspace_id}/members/{member_id}",
    operation_id="removeMember",
    status_code=204,
    response_class=Response,
)
def remove_member(
    workspace_id: uuid.UUID, member_id: uuid.UUID, user: CurrentUser, members: MembershipServiceDep
) -> Response:
    """Remove a member (owners only); their access ends at once. 409 `last_owner` for
    the last owner."""
    members.remove(user, workspace_id, member_id)
    return Response(status_code=204)


@router.post(
    "/{workspace_id}/leave", operation_id="leaveWorkspace", status_code=204, response_class=Response
)
def leave_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, members: MembershipServiceDep
) -> Response:
    """Leave the Workspace (any member). The last owner cannot (409 `last_owner`):
    make someone else an owner first."""
    members.leave(user, workspace_id)
    return Response(status_code=204)


@router.post("/{workspace_id}/transfer-ownership", operation_id="transferOwnership")
def transfer_ownership(
    workspace_id: uuid.UUID,
    body: TransferOwnershipRequest,
    user: CurrentUser,
    members: MembershipServiceDep,
) -> Workspace:
    """Hand ownership to another member (owners only): they become an owner and you an
    editor. Returns the Workspace as you now see it."""
    return _out(members.transfer_ownership(user, workspace_id, body.user_id))


class ActivityActor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    display_name: str


class ActivityItem(BaseModel):
    id: uuid.UUID
    actor: ActivityActor | None = Field(description="Who did it; null if that user is gone.")
    verb: str = Field(description="What was done, e.g. `workspace.updated`, `member.added`.")
    object_type: str = Field(description="What it was done to: `workspace`, `user`, ...")
    object_id: str | None
    object_label: str = Field(description="The object's name (a user's current display name).")
    details: dict[str, Any] | None = Field(
        description='Extra context, e.g. `{"role": "editor"}`; shape depends on the verb.'
    )
    created_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityItem] = Field(description="Newest first.")
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


@router.get("/{workspace_id}/activity", operation_id="listActivity")
def list_activity(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    workspaces: WorkspaceServiceDep,
    activity: ActivityServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> ActivityPage:
    """Who changed what in the Workspace and when, newest first (any member, viewers
    included; 404 for anyone else)."""
    workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
    page = activity.list(workspace_id, limit=limit, cursor=cursor)
    return ActivityPage(
        items=[
            ActivityItem(
                id=i.id,
                actor=ActivityActor.model_validate(i.actor) if i.actor else None,
                verb=i.verb,
                object_type=i.object_type,
                object_id=i.object_id,
                object_label=i.object_label,
                details=i.details,
                created_at=i.created_at,
            )
            for i in page.items
        ],
        next_cursor=page.next_cursor,
    )


AuditChannel = Literal[
    "user",
    "ai",
    "regeneration",
    "sync",
    "propagation",
    "import",
    "platform_change",
    "system_code_change",
]


class AuditEntry(BaseModel):
    id: uuid.UUID
    actor: ActivityActor | None = Field(description="Who did it; null if that user is gone.")
    via: AuditChannel = Field(description="How: by hand, the AI, a sync, an import, ...")
    entity_type: str = Field(description="What kind of entity changed: `kpi`, `member`, ...")
    entity_id: str
    old: dict[str, Any] | None = Field(
        description="The changed fields before the change, so they can be re-entered by "
        "hand; null for a create."
    )
    new: dict[str, Any] | None = Field(
        description="The fields after the change; null for a delete."
    )
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntry] = Field(description="Newest first.")
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


@router.get("/{workspace_id}/audit", operation_id="listAudit")
def list_audit(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    workspaces: WorkspaceServiceDep,
    audit: AuditServiceDep,
    auth: AuthServiceDep,
    entity_type: Annotated[str | None, Query(max_length=64)] = None,
    entity_id: Annotated[str | None, Query(max_length=64, description="One object.")] = None,
    actor_id: uuid.UUID | None = None,
    via: AuditChannel | None = None,
    since: Annotated[datetime | None, Query(description="From this moment, inclusive.")] = None,
    until: Annotated[datetime | None, Query(description="Until this moment, inclusive.")] = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> AuditPage:
    """The audit trail of the Workspace's critical entities, newest first, with old and
    new values (any member, viewers included; 404 for anyone else). Entries are
    read-only: old values are re-entered by hand as a new change, never restored.
    Connection entries never hold secrets, and show host and username to owners only."""
    scope = workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
    page = audit.page(
        workspace_id,
        see_connection_details=can(user, Action.VIEW_CONNECTION, scope),
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        via=via,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )
    actors = auth.users_by_id({e.actor_id for e in page.items if e.actor_id is not None})
    items = []
    for e in page.items:
        actor = actors.get(e.actor_id) if e.actor_id is not None else None
        items.append(
            AuditEntry(
                id=e.id,
                actor=ActivityActor(user_id=actor.id, display_name=actor.display_name)
                if actor
                else None,
                via=e.via,
                entity_type=e.entity_type,
                entity_id=e.entity_id,
                old=e.old,
                new=e.new,
                created_at=e.created_at,
            )
        )
    return AuditPage(items=items, next_cursor=page.next_cursor)
