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

from tests.authz.matrix import (
    Row,
    credentials_of,
    describe,
    outcome_of,
    public,
    registered_routes,
    send,
    signed_in,
    workspace,
)
from tests.roles import ROLES, Role, RoleClients

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
