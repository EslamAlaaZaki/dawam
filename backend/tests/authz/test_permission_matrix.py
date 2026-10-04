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

from dawam.modules.auth import PasswordResets
from dawam.modules.mail import MailService
from tests.authz.matrix import (
    Row,
    admin_only,
    credentials_of,
    describe,
    outcome_of,
    public,
    registered_routes,
    send,
    signed_in,
    workspace,
)
from tests.roles import PASSWORD, ROLES, Role, RoleClients


def _open_registration_and_sign_up(roles: RoleClients) -> dict[str, str]:
    """Sign-up is closed by default: an admin opens it first, through the API."""
    response = roles.client("admin").put(
        "/api/v1/admin/settings",
        json={"registration": {"enabled": True, "allowed_email_domains": []}},
    )
    assert response.status_code == 200, response.text
    return {"email": "newcomer@example.com", "password": PASSWORD, "display_name": "New"}


SMTP_SETTINGS = {
    "host": "smtp.example.com",
    "port": 25,
    "security": "none",
    "sender": "dawam@example.com",
    "username": None,
}


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
        "PATCH",
        "/api/v1/workspaces/{workspace_id}",
        "Rename / edit Workspace details",
        workspace(admin=False, owner=True, editor=False, viewer=False),
        json=lambda roles: {"version": roles.workspace["version"], "name": "Renamed"},
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
