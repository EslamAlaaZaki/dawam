"""The permission suite (spec §10): every endpoint by every role, against the §4.3 matrix.

``ROWS`` has one row per endpoint the app registers, and the suite fails if a
route has none. Adding an endpoint means adding its row here: copy the matching
§4.3 row into ``workspace(...)`` (or use ``public()`` / ``signed_in()``), and give a
``json`` body that an allowed role would succeed with. See ``tests/authz/matrix.py``.
"""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi import FastAPI

from dawam.modules.auth import AuthService, Invitations, PasswordResets, UserAdministration
from dawam.modules.mail import MailService
from dawam.modules.workspaces import WorkspaceService
from tests.authz.matrix import (
    Row,
    admin_only,
    colleague_id,
    credentials_of,
    describe,
    linked_file_id,
    outcome_of,
    public,
    registered_routes,
    rename_pair,
    send,
    signed_in,
    snapshot_id,
    source_table_id,
    system_id,
    table_id,
    tested_model_id,
    use_fake_llm,
    workspace,
)
from tests.roles import PASSWORD, ROLES, Role, RoleClients


def _second_owner(roles: RoleClients) -> None:
    """Makes the colleague an owner too, so the owner is not the last one and may leave."""
    response = roles.client("owner").patch(
        f"/api/v1/workspaces/{roles.workspace_id}/members/{colleague_id(roles)}",
        json={"role": "owner"},
    )
    assert response.status_code == 200, response.text


def _archive(roles: RoleClients) -> None:
    """Archives the Workspace (as the owner), for rows that act on an archived one."""
    state = roles.app.state
    WorkspaceService(state.engine, clock=state.services.clock).archive(
        roles.user("owner"), roles.workspace_id
    )


def _orphaned_workspace_id(roles: RoleClients):
    """A Workspace whose only owner has been deactivated, made through the services."""
    state = roles.app.state
    clock = state.services.clock
    owner = AuthService(state.engine, state.settings, clock=clock).create_user(
        email="departed@example.com",
        password=PASSWORD,
        display_name="Departed",
        system_role="user",
    )
    workspace = WorkspaceService(state.engine, clock=clock).create(owner, name="Orphaned")
    UserAdministration(state.engine, clock=clock).update_user(
        owner.id, is_active=False, actor_id=roles.user("admin").id
    )
    return workspace.id


def _open_registration_and_sign_up(roles: RoleClients) -> dict[str, str]:
    """Sign-up is closed by default: an admin opens it first, through the API."""
    response = roles.client("admin").put(
        "/api/v1/admin/settings",
        json={"registration": {"enabled": True, "allowed_email_domains": []}},
    )
    assert response.status_code == 200, response.text
    return {"email": "newcomer@example.com", "password": PASSWORD, "display_name": "New"}


def _set_up_data_warehouse(roles: RoleClients) -> None:
    """Sets the Data Warehouse up first, so there is something to edit."""
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse",
        json={"target_platform": "postgresql"},
    )
    assert response.status_code == 201, response.text


CONNECTION_BODY = {
    "host": "127.0.0.1",
    "port": 1,
    "database": "source",
    "username": "reader",
    "password": "reader-secret",
    "allowed_schemas": ["public"],
}


def save_a_connection(roles: RoleClients) -> None:
    """Gives the Source System a Connection, so there is one to see."""
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}/connection",
        json=CONNECTION_BODY,
    )
    assert response.status_code in (200, 201), response.text


def save_a_connection_then_archive(roles: RoleClients) -> None:
    """A Connection in an archived Workspace: seeing it is still allowed (owners only)."""
    save_a_connection(roles)
    _archive(roles)


SMTP_SETTINGS = {
    "host": "smtp.example.com",
    "port": 25,
    "security": "none",
    "sender": "dawam@example.com",
    "username": None,
}


LLM_PROVIDER_BODY = {"name": "Local vLLM", "base_url": "http://llm.test/v1", "internal": True}


def _mail(roles: RoleClients) -> MailService:
    state = roles.app.state
    return MailService(
        state.engine, state.settings, sender=state.services.email, clock=state.services.clock
    )


def smtp_test_body(roles: RoleClients) -> dict:
    """SMTP is saved first (the suite's outbox stands in for the server)."""
    _mail(roles).save_smtp_settings(**SMTP_SETTINGS)
    return {"to": "someone@example.com"}


class _LinkCatcher:
    def __init__(self) -> None:
        self.url = ""

    def send(self, message, *, link=None):
        self.url = link.url
        return "sent"

    def withdraw_links(self, *, recipient, purpose):
        pass


def reset_body(roles: RoleClients) -> dict:
    """A fresh reset token for the owner's account, caught from its link."""
    state = roles.app.state
    catcher = _LinkCatcher()
    PasswordResets(
        state.engine, state.settings, mailer=catcher, clock=state.services.clock
    ).request_reset(roles.user("owner").email)
    return {"token": catcher.url.rsplit("token=", 1)[1], "password": "a brand new password"}


def invitation_token(roles: RoleClients) -> str:
    """A fresh invitation token, caught from its link (the admin invites, through the
    auth module)."""
    state = roles.app.state
    catcher = _LinkCatcher()
    Invitations(state.engine, state.settings, mailer=catcher, clock=state.services.clock).invite(
        "invitee@example.com", actor_id=roles.user("admin").id
    )
    return catcher.url.rsplit("token=", 1)[1]


ROWS: list[Row] = [
    # Probes, API docs and version: no data, open to all.
    Row("GET", "/healthz", "liveness probe", public()),
    Row("GET", "/readyz", "readiness probe", public()),
    Row("GET", "/api/v1/openapi.json", "API description", public()),
    Row("GET", "/api/v1/docs", "API docs page", public()),
    Row("GET", "/api/v1/version", "what is running", public()),
    # Authentication.
    Row("POST", "/api/v1/auth/login", "sign in", public(), json=credentials_of("owner")),
    Row("POST", "/api/v1/auth/logout", "sign out", public()),
    Row("GET", "/api/v1/me", "the signed-in user", signed_in()),
    Row(
        "PATCH",
        "/api/v1/me",
        "edit my display name (story 8)",
        signed_in(),
        json=lambda roles: {"display_name": "Renamed"},
    ),
    Row(
        "POST",
        "/api/v1/auth/password/change",
        "change my password (story 7)",
        signed_in(),
        json=lambda roles: {"current_password": PASSWORD, "new_password": "a new passphrase"},
    ),
    Row("POST", "/api/v1/auth/logout-all", "sign out everywhere (story 5)", signed_in()),
    Row("GET", "/api/v1/auth/registration", "is sign-up open? (story 1)", public()),
    Row(
        "POST",
        "/api/v1/auth/register",
        "sign up while self-registration is open (story 1)",
        public(),
        json=_open_registration_and_sign_up,
    ),
    # Administration.
    Row("GET", "/api/v1/admin/settings", "registration settings (story 20)", admin_only()),
    Row(
        "PUT",
        "/api/v1/admin/settings",
        "registration settings (story 20)",
        admin_only(),
        json=lambda roles: {"registration": {"enabled": True, "allowed_email_domains": []}},
    ),
    # LLM gateway: providers, models, setup (stories 155, 156, 158).
    Row("GET", "/api/v1/admin/llm/providers", "list LLM providers", admin_only()),
    Row(
        "POST",
        "/api/v1/admin/llm/providers",
        "register an LLM provider (story 155)",
        admin_only(),
        json=lambda roles: LLM_PROVIDER_BODY,
    ),
    Row("GET", "/api/v1/admin/llm/providers/{provider_id}", "open an LLM provider", admin_only()),
    Row(
        "PUT",
        "/api/v1/admin/llm/providers/{provider_id}",
        "edit an LLM provider (story 155)",
        admin_only(),
        json=lambda roles: LLM_PROVIDER_BODY,
    ),
    Row(
        "DELETE",
        "/api/v1/admin/llm/providers/{provider_id}",
        "remove an LLM provider",
        admin_only(),
    ),
    Row(
        "POST",
        "/api/v1/admin/llm/providers/{provider_id}/models",
        "register a model (story 155)",
        admin_only(),
        json=lambda roles: {"name": "qwen3", "roles": ["agent"]},
    ),
    Row(
        "PUT",
        "/api/v1/admin/llm/models/{model_id}",
        "edit a model (story 155)",
        admin_only(),
        json=lambda roles: {"name": "qwen3-14b", "roles": ["agent"]},
    ),
    Row("DELETE", "/api/v1/admin/llm/models/{model_id}", "remove a model", admin_only()),
    Row(
        "POST",
        "/api/v1/admin/llm/models/{model_id}/test",
        "test connection (story 156)",
        admin_only(),
        setup=use_fake_llm,
    ),
    Row("GET", "/api/v1/admin/llm/setup", "AI setup status (story 158)", admin_only()),
    # Model roles, token budgets and AI usage (stories 157, 159, 162).
    Row("GET", "/api/v1/admin/llm/roles", "see the model roles (story 157)", admin_only()),
    Row(
        "PUT",
        "/api/v1/admin/llm/roles",
        "assign models to roles (story 157)",
        admin_only(),
        json=lambda roles: {"agent_model_id": str(tested_model_id(roles))},
        setup=use_fake_llm,
    ),
    Row("GET", "/api/v1/admin/llm/budgets", "see the token budgets (story 159)", admin_only()),
    Row(
        "PUT",
        "/api/v1/admin/llm/budgets/installation",
        "set the installation's token budget (story 159)",
        admin_only(),
        json=lambda roles: {"monthly_token_budget": 1000},
    ),
    Row(
        "PUT",
        "/api/v1/admin/llm/budgets/workspaces/{workspace_id}",
        "set a Workspace's token budget (story 159)",
        admin_only(),
        json=lambda roles: {"monthly_token_budget": 1000},
    ),
    Row(
        "DELETE",
        "/api/v1/admin/llm/budgets/workspaces/{workspace_id}",
        "remove a Workspace's token budget (story 159)",
        admin_only(),
    ),
    Row(
        "GET",
        "/api/v1/admin/llm/usage",
        "AI usage per Workspace and user (story 162)",
        admin_only(),
    ),
    # User management (stories 14-19, 15a) and the security-event log (story 23).
    Row("GET", "/api/v1/admin/users", "list, search and filter users", admin_only()),
    Row(
        "POST",
        "/api/v1/admin/users",
        "create a user with a temporary password",
        admin_only(),
        json=lambda roles: {
            "email": "new-user@example.com",
            "display_name": "New User",
            "system_role": "user",
            "temporary_password": "temporary password 1",
        },
    ),
    Row(
        "PATCH",
        "/api/v1/admin/users/{user_id}",
        "promote, demote, deactivate, reactivate",
        admin_only(),
        json=lambda roles: {"is_active": True},
    ),
    Row(
        "POST",
        "/api/v1/admin/users/{user_id}/force-reset",
        "force a password reset",
        admin_only(),
    ),
    Row("GET", "/api/v1/admin/security-events", "review security events", admin_only()),
    # Every Workspace's metadata, and the orphan rescue (§4.3, stories 21, 22).
    Row(
        "GET",
        "/api/v1/admin/workspaces",
        "See Workspace in list (metadata only)",
        admin_only(),
    ),
    Row(
        "POST",
        "/api/v1/admin/workspaces/{workspace_id}/reassign-owner",
        "Reassign ownership of an orphaned Workspace (no active owner)",
        admin_only(),
        json=lambda roles: {"user_id": str(roles.user("non_member").id)},
        path_params={"workspace_id": _orphaned_workspace_id},
    ),
    # The signed-in user's own notifications.
    Row("GET", "/api/v1/notifications", "my unread notifications", signed_in()),
    Row("POST", "/api/v1/notifications/read-all", "mark my notifications read", signed_in()),
    Row(
        "POST",
        "/api/v1/notifications/{notification_id}/read",
        "mark one of my notifications read (an id that is not mine changes nothing)",
        signed_in(),
    ),
    # Invitations (stories 10, 15).
    Row(
        "POST",
        "/api/v1/admin/users/invite",
        "invite someone by email",
        admin_only(),
        json=lambda roles: {"email": "invitee@example.com"},
    ),
    Row("GET", "/api/v1/admin/invitations", "see pending invitations", admin_only()),
    Row(
        "DELETE",
        "/api/v1/admin/invitations/{invitation_id}",
        "revoke an invitation",
        admin_only(),
    ),
    Row(
        "POST",
        "/api/v1/auth/invitations/lookup",
        "who an invitation is for: the token is the credential (story 10)",
        public(),
        json=lambda roles: {"token": invitation_token(roles)},
    ),
    Row(
        "POST",
        "/api/v1/auth/invitations/accept",
        "accept an invitation: the token is the credential (story 10)",
        public(),
        json=lambda roles: {
            "token": invitation_token(roles),
            "display_name": "Invitee",
            "password": "a brand new password",
        },
    ),
    Row(
        "POST",
        "/api/v1/auth/password/forgot",
        "forgot password: same answer for any email (story 6)",
        public(),
        json=lambda roles: {"email": roles.user("owner").email},
    ),
    Row(
        "POST",
        "/api/v1/auth/password/reset",
        "reset password: the token is the credential (story 6)",
        public(),
        json=reset_body,
    ),
    # Email (admin only: story 24, §6.1 "Without SMTP").
    Row("GET", "/api/v1/admin/smtp", "see SMTP settings", admin_only()),
    Row(
        "PUT",
        "/api/v1/admin/smtp",
        "configure SMTP",
        admin_only(),
        json=lambda roles: SMTP_SETTINGS,
    ),
    Row("DELETE", "/api/v1/admin/smtp", "turn SMTP off", admin_only()),
    Row(
        "POST",
        "/api/v1/admin/smtp/test",
        "send a test email",
        admin_only(),
        json=smtp_test_body,
    ),
    Row("GET", "/api/v1/admin/undelivered-links", "see links to share", admin_only()),
    Row(
        "DELETE",
        "/api/v1/admin/undelivered-links/{link_id}",
        "remove a link to share",
        admin_only(),
    ),
    # Workspaces.
    Row("GET", "/api/v1/workspaces", "list my Workspaces (story 28)", signed_in()),
    Row(
        "POST",
        "/api/v1/workspaces",
        "a user can create Workspaces (§4.1, story 27)",
        signed_in(),
        json=lambda roles: {"name": "Another", "description": "", "domain": ""},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}",
        "Open Workspace content",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/progress",
        "Open Workspace content (stage progress, story 37)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/activity",
        "Open Workspace content (activity feed, story 38)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/audit",
        "Open Workspace content (audit trail, story 127; Connection host and user are "
        "filtered out for non-owners)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}",
        "Rename / edit Workspace details",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"version": roles.workspace["version"], "name": "Renamed"},
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/archive",
        "Archive / unarchive Workspace",
        workspace(admin=True, owner=True, editor=False, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/unarchive",
        "Archive / unarchive Workspace",
        workspace(admin=True, owner=True, editor=False, viewer=False),
        setup=_archive,
    ),
    Row(
        "DELETE",
        "/api/v1/workspaces/{workspace_id}",
        "Delete Workspace (typed name; admin only if archived)",
        workspace(admin=True, owner=True, editor=False, viewer=False),
        json=lambda roles: {"name": roles.workspace["name"]},
        setup=_archive,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/members",
        "Open Workspace content (the member list)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/members",
        "Add / remove members, change roles",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"email": roles.user("non_member").email, "role": "viewer"},
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/members/{member_id}",
        "Add / remove members, change roles",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"role": "editor"},
    ),
    Row(
        "DELETE",
        "/api/v1/workspaces/{workspace_id}/members/{member_id}",
        "Add / remove members, change roles",
        workspace(admin=False, owner=True, editor=False, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/leave",
        "Leave a Workspace (any member, story 34)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=_second_owner,
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/transfer-ownership",
        "Transfer ownership",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"user_id": str(colleague_id(roles))},
    ),
    # Data Warehouse.
    Row(
        "GET",
        "/api/v1/data-warehouse/platforms",
        "identifier limits and reserved words per target platform (story 87)",
        signed_in(),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        "Open Workspace content (the Data Warehouse setup)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        "Set up the Data Warehouse",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"target_platform": "postgresql"},
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        "Set up the Data Warehouse (settings other than the platform)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        setup=_set_up_data_warehouse,
        json=lambda roles: {"version": 1, "naming_rules": {"case_style": "upper"}},
    ),
    # Source Systems (story 39). Changing a System Code is owner-only: the PATCH row
    # sends no code; tests/sources/test_source_systems.py covers the code change.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems",
        "Open Workspace content (list Source Systems)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems",
        "Create Source Systems",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"name": "CRM", "code": "crm"},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}",
        "Open Workspace content (a Source System)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}",
        "Edit Source Schema enhancements & documents (a Source System's details)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {
            "version": 1,
            "description": "The core banking system",
            "business_owner": "Head of Retail",
        },
    ),
    # KPIs (stories 73, 74; §4.3 "Edit KPIs, DW Schema, mappings").
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/kpis",
        "Open Workspace content (list KPIs)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/kpis",
        "Edit KPIs (document one)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"name": "Customer count"},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}",
        "Open Workspace content (a KPI)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}",
        "Edit KPIs (edit one, move it between statuses)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"version": 1, "unit": "customers"},
    ),
    Row(
        "DELETE",
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}",
        "Edit KPIs (delete one)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    # Files and documents (stories 64, 67): viewers download, editors upload.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
        "Open Workspace content (a Source System's file area)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
        "Upload, edit, delete Workspace files (upload)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        files=lambda roles: {"file": ("notes.md", b"# Notes", "text/markdown")},
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files/data-dictionary",
        "Upload, edit, delete Workspace files (save the data dictionary)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        setup=snapshot_id,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/download",
        "Open Workspace content (download a file)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    # File areas (stories 163-165): viewers list, download and zip; editors edit.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files/download",
        "Open Workspace content (a Source System's file area as a zip)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/data-warehouse/files",
        "Open Workspace content (the Data Warehouse's file area)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=_set_up_data_warehouse,
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/data-warehouse/files",
        "Upload, edit, delete Workspace files (upload to the Data Warehouse)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        setup=_set_up_data_warehouse,
        files=lambda roles: {"file": ("ddl.sql", b"select 1", "text/plain")},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/data-warehouse/files/download",
        "Open Workspace content (the Data Warehouse's file area as a zip)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=_set_up_data_warehouse,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/content",
        "Open Workspace content (a text file's content)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "PUT",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/content",
        "Upload, edit, delete Workspace files (edit a text file)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"content": "# Edited"},
    ),
    Row(
        "PUT",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/replace",
        "Upload, edit, delete Workspace files (replace a file)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        files=lambda roles: {"file": ("sad.md", b"# Replaced", "text/markdown")},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/documents/search",
        "Open Workspace content (search documents)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/documents/reindex",
        "Upload, edit, delete Workspace files (re-index documents)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    # External links and document-to-object links (stories 65, 66): viewers read,
    # editors change (§4.3 "Upload, edit, delete Workspace files" and Source System edits).
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/links",
        "Open Workspace content (a Source System's links)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/links",
        "Edit a Source System (link a page)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {
            "kind": "repo",
            "title": "Core repo",
            "url": "https://git.example.com/cbs",
        },
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/links/{source_link_id}",
        "Edit a Source System (edit a link)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"note": "Updated"},
    ),
    Row(
        "DELETE",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/links/{source_link_id}",
        "Edit a Source System (remove a link)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/object-links",
        "Open Workspace content (a document's links)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/object-links",
        "Upload, edit, delete Workspace files (link a document)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"object_type": "table", "object_id": str(source_table_id(roles))},
    ),
    Row(
        "DELETE",
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/object-links/{object_type}/{object_id}",
        "Upload, edit, delete Workspace files (unlink a document)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        path_params={"file_id": linked_file_id},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/objects/{object_type}/{object_id}/documents",
        "Open Workspace content (documents linked to a table)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    # Background jobs (story 46). Members see every job of the Workspace; they cancel
    # their own, and only an owner cancels one somebody else started (the job here is
    # the owner's, so the owner is its creator and an editor is not).
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/jobs",
        "Open Workspace content (list jobs)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/jobs/{job_id}",
        "Open Workspace content (a job's status, progress and log)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/jobs/{job_id}/cancel",
        "Cancel a job (your own; anyone's is owner-only)",
        workspace(admin=False, owner=True, editor=False, viewer=False),
    ),
    # Connections (stories 40-43): owners only, including seeing them (host and user are
    # owner-only information). Nothing here connects anywhere that answers. Seeing one
    # (connection.view) works in an archived Workspace too; changing one does not.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/connection",
        "Create Connection, edit credentials (see a Connection, even archived)",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        setup=save_a_connection_then_archive,
    ),
    Row(
        "PUT",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/connection",
        "Create Connection, edit credentials",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: CONNECTION_BODY,
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/connection/test",
        "Create Connection, edit credentials (test a Connection)",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: CONNECTION_BODY,
    ),
    # Extraction (stories 45, 46): owners and editors run it (the job runs and fails
    # against a source that does not answer; starting it is what is checked). Every
    # member reads the Snapshots.
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/extractions",
        "Run extraction / Schema Import / profiling",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        setup=save_a_connection,
    ),
    # Profiling (stories 55-57): owners and editors run it, only owners switch top-N on
    # per table, every member reads the profiles.
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/profiling",
        "Run extraction / Schema Import / profiling (profile tables)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"table_ids": [table_id(roles)]},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profile",
        "Open Workspace content (a table's profile)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/columns/{column_id}/profile",
        "Open Workspace content (a column's profile)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    Row(
        "PUT",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profiling-settings",
        "Switch top-N value profiling on or off for a table",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"enabled": False},
    ),  # Schema Import (stories 47, 49-51): owners and editors download the template, validate
    # and upload, and ask for a Connection; every member sees the disabled features.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/template",
        "Run extraction / Schema Import / profiling (the template)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/validate",
        "Run extraction / Schema Import / profiling (validate an upload)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        files=lambda roles: {"files": ("tables.csv", b"schema,table,kind\n", "text/csv")},
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/upload",
        "Run extraction / Schema Import / profiling (upload an import)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        files=lambda roles: {"files": ("tables.csv", b"schema,table,kind\n", "text/csv")},
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import",
        "Open Workspace content (what an imported system cannot do)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/connection-requests",
        "Run extraction / Schema Import / profiling (ask for a Connection)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/data-dictionary",
        "Open Workspace content (the data dictionary as XLSX)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/snapshots",
        "Open Workspace content (list Snapshots)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/snapshots/{snapshot_id}",
        "Open Workspace content (a Snapshot's catalog)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/snapshots/{snapshot_id}/diff/{against_id}",
        "Open Workspace content (diff two Snapshots)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        path_params={"against_id": snapshot_id},
    ),
    # Source Schema browser (story 54): every member browses and searches it.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/schema",
        "Open Workspace content (the Source Schema)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/schema/search",
        "Open Workspace content (search the Source Schema)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    # Source enhancements (stories 61, 62): owners and editors.
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}",
        "Edit Source Schema enhancements (a table)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"version": 1, "tags": ["PII"]},
    ),
    Row(
        "PATCH",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/columns/{column_id}",
        "Edit Source Schema enhancements (a column)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"version": 1, "is_sensitive": True},
    ),
    # Rename candidates and manual merges (story 52a): owners and editors decide; every
    # member sees the candidates.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/rename-candidates",
        "Open Workspace content (rename candidates)",
        workspace(admin=False, owner=True, editor=True, viewer=True),
        setup=snapshot_id,
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/rename-candidates/{candidate_id}/confirm",
        "Edit Source Schema enhancements (confirm a rename)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/rename-candidates/{candidate_id}/reject",
        "Edit Source Schema enhancements (reject a rename)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/renames",
        "Edit Source Schema enhancements (merge a removed object into an added one)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {
            "object_type": "column",
            "removed_id": str((pair := rename_pair(roles))[1]),
            "added_id": str(pair[2]),
        },
    ),
    # PII review queue (stories 133-135; §4.3 "Review PII findings"): owners and editors.
    Row(
        "GET",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings",
        "Review PII findings (the queue)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        setup=snapshot_id,
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings/{finding_id}/confirm",
        "Review PII findings (confirm)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings/{finding_id}/dismiss",
        "Review PII findings (dismiss)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
    ),
    Row(
        "POST",
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-scans",
        "Review PII findings (start a value-based scan)",
        workspace(admin=False, owner=True, editor=True, viewer=False),
        json=lambda roles: {"table_ids": [table_id(roles)]},
    ),
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("row", ROWS, ids=str)
def test_each_role_gets_what_the_matrix_says(row: Row, role: Role, roles: RoleClients):
    response = send(row, role, roles)

    expected = row.expect[role]
    assert outcome_of(response.status_code) == expected, (
        f"{row} as {role} ({row.matrix}): expected {expected.value}, "
        f"got {response.status_code}: {response.text[:300]}"
    )


def test_every_registered_route_has_a_row(app: FastAPI):
    missing = registered_routes(app) - {row.key for row in ROWS}
    assert not missing, (
        "These routes have no row in the permission suite. Add one per route to ROWS in "
        f"tests/authz/test_permission_matrix.py, from the spec's §4.3 matrix:\n{describe(missing)}"
    )


def test_every_row_is_a_registered_route(app: FastAPI):
    stale = {row.key for row in ROWS} - registered_routes(app)
    assert not stale, f"These rows match no registered route:\n{describe(stale)}"


def test_each_endpoint_has_one_row_covering_every_role():
    duplicated = [key for key, count in Counter(row.key for row in ROWS).items() if count > 1]
    assert not duplicated, f"Endpoints with more than one row:\n{describe(duplicated)}"
    incomplete = [str(row) for row in ROWS if set(row.expect) != set(ROLES)]
    assert not incomplete, f"Rows that miss a role: {incomplete}"
