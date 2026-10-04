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
- ``Workspace``, ``WorkspacePage``, ``WorkspaceRole``: what the service returns.
- ``router``: ``GET|POST /workspaces``, ``GET|PATCH /workspaces/{workspace_id}``,
  ``GET /workspaces/{workspace_id}/progress``.

Owns the ``workspaces`` and ``workspace_members`` tables.
"""

from .api import router
from .internal.policy import INSTALLATION, Action, Installation, WorkspaceRole, WorkspaceScope, can
from .service import Workspace, WorkspacePage, WorkspaceService

__all__ = [
    "INSTALLATION",
    "Action",
    "Installation",
    "Workspace",
    "WorkspacePage",
    "WorkspaceRole",
    "WorkspaceScope",
    "WorkspaceService",
    "can",
    "router",
]
