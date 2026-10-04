"""The stage-progress endpoint (spec story 37): where a Workspace's work stands."""

from __future__ import annotations

from tests.roles import RoleClients

NOT_STARTED = "not_started"


def test_a_new_workspace_has_nothing_started(roles: RoleClients):
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace['id']}/progress")

    assert response.status_code == 200
    assert response.json() == {
        # No Source Systems yet, so there is no Source Analysis to report.
        "source_analysis": [],
        "kpis": {"status": NOT_STARTED},
        "dw_modeling": [
            {"layer": "staging", "status": NOT_STARTED},
            {"layer": "core", "status": NOT_STARTED},
            {"layer": "mart", "status": NOT_STARTED},
        ],
    }


def test_viewers_and_editors_see_the_same_progress_as_owners(roles: RoleClients):
    path = f"/api/v1/workspaces/{roles.workspace['id']}/progress"
    seen = {role: roles.client(role).get(path) for role in ("owner", "editor", "viewer")}

    assert {r.status_code for r in seen.values()} == {200}
    assert seen["editor"].json() == seen["owner"].json() == seen["viewer"].json()


def test_non_members_get_404_for_progress(roles: RoleClients):
    path = f"/api/v1/workspaces/{roles.workspace['id']}/progress"

    assert roles.client("non_member").get(path).status_code == 404
    assert roles.client("admin").get(path).status_code == 404
