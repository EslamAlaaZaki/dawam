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
- An archived Workspace is read-only (spec story 35): a rule says whether it applies
  to ``active`` Workspaces (the default, so every content-changing action, including
  those of later modules, is refused once archived), to ``archived`` ones, or to
  ``any``. ``WorkspaceScope.archived`` carries the state, resolved on the server.

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
    ARCHIVE_WORKSPACE = "workspace.archive"
    """Make an active Workspace read-only (owners, and admins whether members or not)."""
    UNARCHIVE_WORKSPACE = "workspace.unarchive"
    """Make an archived Workspace editable again (owners and admins)."""
    DELETE_WORKSPACE = "workspace.delete"
    """Delete a Workspace for good, after typing its name (owners; admins who are not
    members only once it is archived)."""
    REASSIGN_OWNERSHIP = "workspace.reassign_ownership"
    """Give an orphaned Workspace a new owner (admins only). The policy only says who
    may ask: the service refuses unless the server confirms no active owner is left."""
    LEAVE_WORKSPACE = "workspace.leave"
    """Leave the Workspace (any member; spec story 34). The last owner still cannot."""
    SET_UP_DATA_WAREHOUSE = "data_warehouse.set_up"
    """Set up the Data Warehouse and edit its Layer schema names, naming rules and date
    dimension settings (spec §4.3, story 87)."""
    CHANGE_AI_SETTINGS = "workspace.ai_settings"
    """Choose the Workspace's agent model, its internal-only restriction and its
    data-sharing level (owners only; spec §4.3, stories 160, 161)."""
    ASK_ASSISTANT = "assistant.ask"
    """Start and share conversations with the assistant and ask it questions (every member,
    viewers too; archived Workspaces make the chat read-only; spec §4.3, stories 149, 151).
    Reading conversations needs only ``VIEW_WORKSPACE``. What the assistant may *do* is
    decided per tool, by the action of the tool's own rule (a viewer's chat cannot write)."""
    CHANGE_DW_PLATFORM = "data_warehouse.change_platform"
    """Change the Data Warehouse's target platform after setup (owners only; spec §4.3)."""
    EDIT_DW_SCHEMA = "dw_schema.edit"
    """Create, edit and delete the DW Schema's Core and Mart tables and columns (owners and
    editors; spec §4.3 "Edit KPIs, DW Schema, mappings", stories 89-93a). Reading the model
    needs only ``VIEW_WORKSPACE``."""
    CREATE_SOURCE_SYSTEM = "source_system.create"
    """Add a Source System to the Workspace (owners and editors; spec story 39)."""
    EDIT_SOURCE_SYSTEM = "source_system.edit"
    """Edit a Source System's name, description and owners (owners and editors)."""
    CHANGE_SYSTEM_CODE = "source_system.change_code"
    """Change a Source System's System Code after creation (owners only)."""
    RUN_EXTRACTION = "source_system.extract"
    """Extract a Source System's metadata from its Connection into a Snapshot (owners and
    editors; spec §4.3 "Run extraction / Schema Import / profiling", story 45)."""
    EDIT_SOURCE_ENHANCEMENTS = "source_schema.enhance"
    """Add descriptions, tags, a sensitivity flag, a classification and an SCD hint to a
    Source System's tables and columns (owners and editors; spec stories 61, 62)."""
    RUN_PROFILING = "source_system.profile"
    """Profile selected tables of a Source System through its Connection (owners and
    editors; spec §4.3 "Run extraction / Schema Import / profiling", story 55)."""
    RUN_SOURCE_QUERY = "source_system.query"
    """Have the assistant run guarded read-only queries on a Source System's live Connection
    (owners and editors; the Workspace must also share sample rows; spec §6.8, story 68)."""
    ENABLE_TOP_N = "source_table.top_n"
    """Switch top-N value capture on or off for one table (owners only; spec §6.5)."""
    REVIEW_PII = "pii.review"
    """List PII findings and confirm or dismiss them in the review queue (owners and
    editors; spec §4.3 "Review PII findings", stories 133-135)."""
    MANAGE_PII_RULES = "pii.manage_rules"
    """List, add, edit and delete the Workspace's custom PII rules and switch built-in rules
    on or off (owners only; spec §6.12, story 136)."""
    CANCEL_OWN_JOB = "job.cancel_own"
    """Cancel a background job the user started (any member who started one)."""
    CANCEL_ANY_JOB = "job.cancel_any"
    """Cancel a background job somebody else started (owners only)."""
    LIST_ALL_WORKSPACES = "installation.list_workspaces"
    """See every Workspace's metadata, never its content (admins only; spec §4.3)."""
    EDIT_KPI = "kpi.edit"
    """Create, edit and delete KPIs, and move them between statuses (owners and editors;
    spec §4.3, stories 73, 74, 80)."""
    COMMENT = "comment.create"
    """Comment on tables, columns, DW objects, KPIs and mappings, @mention members, and
    resolve or reopen threads (owners, editors and viewers; spec §4.3 "Comment", stories
    107, 125, 126). Reading comments needs only ``VIEW_WORKSPACE``."""
    UPLOAD_FILE = "file.upload"
    """Upload a file or document to a Source System's file area (owners and editors;
    spec §4.3 "Upload, edit, delete Workspace files", story 64). Listing and downloading
    files need only ``VIEW_WORKSPACE``."""
    VIEW_CONNECTION = "connection.view"
    """See a Source System's Connection, including its host and user but never its
    password (owners only, archived Workspaces too; spec §4.3)."""
    MANAGE_CONNECTION = "connection.manage"
    """Test, create and edit a Source System's Connection and its credentials (owners
    only; spec §4.3)."""
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
    archived: bool
    """The Workspace is archived, hence read-only."""


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
    state: Literal["active", "archived", "any"] = "active"
    """The Workspace states it applies to. Anything that changes content keeps the
    default: an archived Workspace is read-only."""
    admin_needs_archived: bool = False
    """Admins who are not members may act only on an archived Workspace."""


_RULES: dict[Action, _SystemRule | _WorkspaceRule] = {
    Action.CREATE_WORKSPACE: _SystemRule(),
    Action.MANAGE_EMAIL: _SystemRule(admin_only=True),
    Action.MANAGE_USERS: _SystemRule(admin_only=True),
    Action.VIEW_SECURITY_EVENTS: _SystemRule(admin_only=True),
    Action.VIEW_WORKSPACE: _WorkspaceRule(min_role="viewer", state="any"),
    Action.EDIT_WORKSPACE: _WorkspaceRule(min_role="owner"),
    Action.MANAGE_MEMBERS: _WorkspaceRule(min_role="owner"),
    Action.TRANSFER_OWNERSHIP: _WorkspaceRule(min_role="owner"),
    Action.SET_UP_DATA_WAREHOUSE: _WorkspaceRule(min_role="editor"),
    Action.CHANGE_AI_SETTINGS: _WorkspaceRule(min_role="owner"),
    Action.ASK_ASSISTANT: _WorkspaceRule(min_role="viewer"),
    Action.CHANGE_DW_PLATFORM: _WorkspaceRule(min_role="owner"),
    Action.EDIT_DW_SCHEMA: _WorkspaceRule(min_role="editor"),
    Action.CREATE_SOURCE_SYSTEM: _WorkspaceRule(min_role="editor"),
    Action.EDIT_SOURCE_SYSTEM: _WorkspaceRule(min_role="editor"),
    Action.CHANGE_SYSTEM_CODE: _WorkspaceRule(min_role="owner"),
    Action.ARCHIVE_WORKSPACE: _WorkspaceRule(min_role="owner", admin=True),
    Action.UNARCHIVE_WORKSPACE: _WorkspaceRule(min_role="owner", admin=True, state="archived"),
    Action.DELETE_WORKSPACE: _WorkspaceRule(
        min_role="owner", admin=True, state="any", admin_needs_archived=True
    ),
    Action.REASSIGN_OWNERSHIP: _WorkspaceRule(min_role=None, admin=True, state="any"),
    Action.LEAVE_WORKSPACE: _WorkspaceRule(min_role="viewer", state="any"),
    Action.RUN_EXTRACTION: _WorkspaceRule(min_role="editor"),
    Action.EDIT_SOURCE_ENHANCEMENTS: _WorkspaceRule(min_role="editor"),
    Action.RUN_PROFILING: _WorkspaceRule(min_role="editor"),
    Action.RUN_SOURCE_QUERY: _WorkspaceRule(min_role="editor"),
    Action.ENABLE_TOP_N: _WorkspaceRule(min_role="owner"),
    Action.REVIEW_PII: _WorkspaceRule(min_role="editor"),
    Action.MANAGE_PII_RULES: _WorkspaceRule(min_role="owner"),
    Action.CANCEL_OWN_JOB: _WorkspaceRule(min_role="viewer"),
    Action.CANCEL_ANY_JOB: _WorkspaceRule(min_role="owner"),
    Action.LIST_ALL_WORKSPACES: _SystemRule(admin_only=True),
    Action.EDIT_KPI: _WorkspaceRule(min_role="editor"),
    Action.COMMENT: _WorkspaceRule(min_role="viewer"),
    Action.UPLOAD_FILE: _WorkspaceRule(min_role="editor"),
    Action.VIEW_CONNECTION: _WorkspaceRule(min_role="owner", state="any"),
    Action.MANAGE_CONNECTION: _WorkspaceRule(min_role="owner"),
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
    if (rule.state == "active" and resource.archived) or (
        rule.state == "archived" and not resource.archived
    ):
        return False
    if rule.admin and is_admin and (resource.archived or not rule.admin_needs_archived):
        return True
    return (
        resource.role is not None
        and rule.min_role is not None
        and _RANK[resource.role] >= _RANK[rule.min_role]
    )
