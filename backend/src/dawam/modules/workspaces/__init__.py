"""Workspaces module: Workspaces, their members and the authorization policy.

Public interface. Other modules import only what is re-exported here:

- ``can(user, action, resource)``: the one authorization policy (spec §6.2). Every
  endpoint and assistant tool goes through it, usually via ``WorkspaceService``;
  nothing else compares roles. ``Action`` names what is being done, and a resource is
  ``INSTALLATION`` or a ``WorkspaceScope``.
- ``WorkspaceService``: create, list, open and edit Workspaces, add members, and
  ``authorize(user, action, workspace_id)``, which a module with Workspace-scoped
  resources calls with the ``workspace_id`` it read from its own resource (never one
  from client input), getting 404 for non-members and 403 for too low a role.
- ``required_role(action)``: the lowest role that may perform an action, which a Change
  Set item carries as its required role.
- ``Workspace``, ``WorkspacePage``, ``WorkspaceRole``: what the service returns.
- ``MembershipService``: list members (``Member``), add someone by email or invite
  them into the Workspace (``MemberAdded`` / ``MemberInvited``), change roles, remove
  members, leave and transfer ownership; a Workspace always keeps an owner.
- ``InvitedWorkspaceMembership``: the auth module's ``InvitedMembership`` port, which
  the composition root sets as ``app.state.invited_membership``.
- ``WorkspaceAdministration``: what admins do across Workspaces, seeing metadata only
  (``AdminWorkspace`` / ``AdminWorkspacePage``): list them all, and reassign ownership
  of one whose owners are all deactivated.
- Archived Workspaces are read-only: ``WorkspaceScope.archived`` makes ``can`` refuse
  every action that changes anything; only viewing, leaving, unarchiving, deleting and
  reassigning ownership still pass. ``WorkspaceService.authorize`` then says 409
  ``workspace_archived``.
- ``router``: ``GET|POST /workspaces``, ``GET|PATCH|DELETE /workspaces/{workspace_id}``,
  ``POST .../archive``, ``POST .../unarchive``,
  ``GET /workspaces/{workspace_id}/progress``, ``GET .../activity`` (the activity feed,
  served from the ``activity`` module's service), ``GET|POST .../members``,
  ``PATCH|DELETE .../members/{member_id}``, ``POST .../leave``,
  ``POST .../transfer-ownership``.

Owns the ``workspaces`` and ``workspace_members`` tables.
"""

from .api import router
from .internal.administration import (
    AdminWorkspace,
    AdminWorkspacePage,
    WorkspaceAdministration,
    WorkspaceOwner,
)
from .internal.members import (
    InvitedWorkspaceMembership,
    Member,
    MemberAdded,
    MemberInvited,
    MembershipService,
)
from .internal.policy import (
    INSTALLATION,
    WORKSPACE_ACTIONS,
    Action,
    Installation,
    WorkspaceRole,
    WorkspaceScope,
    can,
    required_role,
)
from .service import Workspace, WorkspacePage, WorkspaceService

__all__ = [
    "INSTALLATION",
    "WORKSPACE_ACTIONS",
    "Action",
    "AdminWorkspace",
    "AdminWorkspacePage",
    "Installation",
    "InvitedWorkspaceMembership",
    "Member",
    "MemberAdded",
    "MemberInvited",
    "MembershipService",
    "Workspace",
    "WorkspaceAdministration",
    "WorkspaceOwner",
    "WorkspacePage",
    "WorkspaceRole",
    "WorkspaceScope",
    "WorkspaceService",
    "can",
    "required_role",
    "router",
]
