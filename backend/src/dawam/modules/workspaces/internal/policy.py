"""The authorization policy (spec §4.3, §6.2): the one place that decides who may do what.

``can(user, action, resource)`` answers every authorization question in DAWAM.
Endpoints, services and (later) assistant tools ask it; nothing else compares roles.

- An ``Action`` names one row of the spec's permission matrix (§4.3).
- A resource is either ``INSTALLATION`` (system-wide actions, such as creating a
  Workspace) or a ``WorkspaceScope``: the Workspace a resource belongs to, resolved
  on the server from the resource itself, together with the role the user being
  checked holds there (``None`` for a non-member). Every Workspace-scoped resource
  (a Source System, a KPI, a job, ...) is checked through the scope of its Workspace;
  ``WorkspaceService.authorize`` resolves it.
- Each action has exactly one rule in ``_RULES``. Workspace roles are ordered
  ``viewer < editor < owner``, so a rule names the lowest role that may act; it may
  also let admins act whether or not they are members (the matrix's "Admin
  (non-member)" column).

The same rules are meant to drive a Change Set item's required role and the
assistant's tool checks later; they must not grow a second copy of this table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, get_args

from dawam.modules.auth import User

WorkspaceRole = Literal["viewer", "editor", "owner"]
WORKSPACE_ROLES: tuple[WorkspaceRole, ...] = get_args(WorkspaceRole)
"""Lowest to highest: each role may do everything the ones before it may."""

_RANK: dict[str, int] = {role: rank for rank, role in enumerate(WORKSPACE_ROLES)}


class Action(StrEnum):
    """What a user wants to do: one row of the spec's permission matrix (§4.3)."""

    CREATE_WORKSPACE = "workspace.create"
    """Create a Workspace (any signed-in user; spec §4.1)."""
    VIEW_WORKSPACE = "workspace.view"
    """Open Workspace content."""
    EDIT_WORKSPACE = "workspace.edit"
    """Rename / edit Workspace details."""
    MANAGE_MEMBERS = "workspace.manage_members"
    """Add / remove members, change roles."""
    TRANSFER_OWNERSHIP = "workspace.transfer_ownership"
    """Hand ownership to another member, stepping down to editor."""
    LEAVE_WORKSPACE = "workspace.leave"
    """Leave the Workspace (any member; spec story 34). The last owner still cannot."""
    MANAGE_SYSTEM_SETTINGS = "installation.manage_settings"
    """Read and change the installation-wide settings, e.g. self-registration (admins
    only; spec §4.1, story 20)."""
    MANAGE_USERS = "installation.manage_users"
    """List, create, invite, deactivate, promote and demote users and force password resets
    (admins only; spec stories 14-19)."""
    VIEW_SECURITY_EVENTS = "installation.view_security_events"
    """Review the security-event log (admins only; spec story 23)."""
    MANAGE_EMAIL = "installation.manage_email"
    """Configure SMTP, send a test email and see the links DAWAM could not email
    (admins only; spec story 24, §6.1 "Without SMTP")."""


@dataclass(frozen=True)
class Installation:
    """The installation as a whole: the resource of system-wide actions."""


INSTALLATION = Installation()


@dataclass(frozen=True)
class WorkspaceScope:
    """A Workspace-scoped resource as the policy sees it.

    ``workspace_id`` comes from the resource on the server, never from client input;
    ``role`` is what ``user_id`` holds in that Workspace (``None``: not a member).
    """

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: WorkspaceRole | None


Resource = Installation | WorkspaceScope


@dataclass(frozen=True)
class _SystemRule:
    admin_only: bool = False
    """Only admins may; otherwise every signed-in user may."""


@dataclass(frozen=True)
class _WorkspaceRule:
    min_role: WorkspaceRole | None
    """The lowest Workspace role that may; ``None``: no Workspace role may."""
    admin: bool = False
    """Admins may too, member or not."""


_RULES: dict[Action, _SystemRule | _WorkspaceRule] = {
    Action.CREATE_WORKSPACE: _SystemRule(),
    Action.MANAGE_EMAIL: _SystemRule(admin_only=True),
    Action.MANAGE_USERS: _SystemRule(admin_only=True),
    Action.VIEW_SECURITY_EVENTS: _SystemRule(admin_only=True),
    Action.VIEW_WORKSPACE: _WorkspaceRule(min_role="viewer"),
    Action.EDIT_WORKSPACE: _WorkspaceRule(min_role="owner"),
    Action.MANAGE_MEMBERS: _WorkspaceRule(min_role="owner"),
    Action.TRANSFER_OWNERSHIP: _WorkspaceRule(min_role="owner"),
    Action.LEAVE_WORKSPACE: _WorkspaceRule(min_role="viewer"),
    Action.MANAGE_SYSTEM_SETTINGS: _SystemRule(admin_only=True),
}

if set(_RULES) != set(Action):  # pragma: no cover - a programming error, caught on import
    raise RuntimeError(f"actions without a rule: {sorted(set(Action) - set(_RULES))}")

WORKSPACE_ACTIONS: tuple[Action, ...] = tuple(
    action for action, rule in _RULES.items() if isinstance(rule, _WorkspaceRule)
)
"""The actions whose resource is a ``WorkspaceScope``."""


def can(user: User, action: Action, resource: Resource) -> bool:
    """Whether ``user`` may perform ``action`` on ``resource``.

    Raises ``TypeError`` if the resource is of the wrong kind for the action, and
    ``ValueError`` if a ``WorkspaceScope`` was resolved for someone else: both are
    programming errors, never a "no".
    """
    rule = _RULES[action]
    is_admin = user.system_role == "admin"
    if isinstance(rule, _SystemRule):
        if not isinstance(resource, Installation):
            raise TypeError(f"{action} applies to the installation, not {resource!r}")
        return is_admin or not rule.admin_only
    if not isinstance(resource, WorkspaceScope):
        raise TypeError(f"{action} applies to a Workspace, not {resource!r}")
    if resource.user_id != user.id:
        raise ValueError("the Workspace scope was resolved for a different user")
    if rule.admin and is_admin:
        return True
    return (
        resource.role is not None
        and rule.min_role is not None
        and _RANK[resource.role] >= _RANK[rule.min_role]
    )
