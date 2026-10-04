"""Creating, listing, opening and editing Workspaces (spec stories 27, 28; §8.3)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from tests.roles import RoleClients


def create(client: TestClient, name: str, **fields: str) -> dict:
    response = client.post("/api/v1/workspaces", json={"name": name, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_creating_a_workspace_makes_the_creator_its_owner(signed_in_client: TestClient, clock):
    response = signed_in_client.post(
        "/api/v1/workspaces",
        json={
            "name": "  Retail DW  ",
            "description": "Warehouse for the retail bank",
            "domain": "Retail banking",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created == {
        "id": created["id"],
        "name": "Retail DW",
        "description": "Warehouse for the retail bank",
        "domain": "Retail banking",
        "role": "owner",
        "permissions": ["workspace.edit", "workspace.manage_members", "workspace.view"],
        "version": 1,
        "created_at": clock().isoformat().replace("+00:00", "Z"),
        "updated_at": clock().isoformat().replace("+00:00", "Z"),
    }
    opened = signed_in_client.get(f"/api/v1/workspaces/{created['id']}")
    assert opened.status_code == 200
    assert opened.json() == created


def test_description_and_domain_are_optional(signed_in_client: TestClient):
    created = create(signed_in_client, "Bare")

    assert (created["description"], created["domain"]) == ("", "")


def test_a_workspace_needs_a_name(signed_in_client: TestClient):
    for body in ({}, {"name": ""}, {"name": "   "}, {"name": "x" * 201}):
        response = signed_in_client.post("/api/v1/workspaces", json=body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error"

    assert signed_in_client.get("/api/v1/workspaces").json()["items"] == []


def test_the_list_shows_only_my_workspaces_with_my_role(roles: RoleClients):
    shared = roles.workspace
    roles.client("viewer")  # joins the shared Workspace as a viewer
    own = create(roles.client("viewer"), "Viewer's own")
    create(roles.client("non_member"), "Someone else's")

    def listed(role):
        response = roles.client(role).get("/api/v1/workspaces")
        assert response.status_code == 200
        return [(w["id"], w["name"], w["role"]) for w in response.json()["items"]]

    assert listed("viewer") == [
        (shared["id"], "Permission Matrix", "viewer"),
        (own["id"], "Viewer's own", "owner"),
    ]
    assert listed("owner") == [(shared["id"], "Permission Matrix", "owner")]
    assert listed("admin") == []  # admins see no Workspace they are not a member of


def test_the_list_is_ordered_by_name_and_paged_with_a_cursor(signed_in_client: TestClient):
    for name in ("gamma", "Alpha", "beta"):
        create(signed_in_client, name)

    first = signed_in_client.get("/api/v1/workspaces", params={"limit": 2}).json()
    assert [w["name"] for w in first["items"]] == ["Alpha", "beta"]
    assert first["next_cursor"]

    second = signed_in_client.get(
        "/api/v1/workspaces", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [w["name"] for w in second["items"]] == ["gamma"]
    assert second["next_cursor"] is None


def test_a_bad_cursor_or_limit_is_rejected(signed_in_client: TestClient):
    for cursor in ("not-a-cursor", "WyJhIl0", "WyJhIiwiYiJd"):  # junk, ["a"], ["a","b"]
        response = signed_in_client.get("/api/v1/workspaces", params={"cursor": cursor})
        assert response.status_code == 422, cursor
        assert response.json()["error"]["code"] == "invalid_cursor"
    for limit in (0, 101):
        response = signed_in_client.get("/api/v1/workspaces", params={"limit": limit})
        assert response.status_code == 422, limit


def test_an_owner_edits_the_details(roles: RoleClients, clock):
    owner = roles.client("owner")
    workspace = roles.workspace
    clock.advance(timedelta(minutes=5))

    response = owner.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={
            "version": 1,
            "name": "Renamed",
            "description": "Now described",
            "domain": "Insurance",
        },
    )

    assert response.status_code == 200
    edited = response.json()
    assert edited == {
        **workspace,
        "name": "Renamed",
        "description": "Now described",
        "domain": "Insurance",
        "version": 2,
        "updated_at": clock().isoformat().replace("+00:00", "Z"),
    }
    assert owner.get(f"/api/v1/workspaces/{workspace['id']}").json() == edited
    assert owner.get("/api/v1/workspaces").json()["items"] == [edited]


def test_fields_left_out_of_an_edit_stay_as_they_are(roles: RoleClients):
    owner = roles.client("owner")
    workspace = roles.workspace

    edited = owner.patch(
        f"/api/v1/workspaces/{workspace['id']}", json={"version": 1, "domain": ""}
    ).json()

    assert (edited["name"], edited["description"], edited["domain"]) == (
        "Permission Matrix",
        "",
        "",
    )


def test_an_edit_with_a_stale_version_is_rejected_with_409(roles: RoleClients):
    owner = roles.client("owner")
    url = f"/api/v1/workspaces/{roles.workspace['id']}"
    assert owner.patch(url, json={"version": 1, "name": "First edit"}).status_code == 200

    response = owner.patch(url, json={"version": 1, "name": "Lost update"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    assert response.json()["error"]["details"] == {"current_version": 2}
    current = owner.get(url).json()
    assert (current["name"], current["version"]) == ("First edit", 2)


def test_editors_and_viewers_open_a_workspace_but_cannot_edit_it(roles: RoleClients):
    url = f"/api/v1/workspaces/{roles.workspace['id']}"
    for role in ("editor", "viewer"):
        client = roles.client(role)

        opened = client.get(url)
        assert opened.status_code == 200
        assert (opened.json()["role"], opened.json()["permissions"]) == (role, ["workspace.view"])

        response = client.patch(url, json={"version": 1, "name": f"Renamed by {role}"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    assert roles.client("owner").get(url).json()["name"] == "Permission Matrix"


def test_non_members_and_admins_cannot_tell_a_workspace_exists(roles: RoleClients):
    real = f"/api/v1/workspaces/{roles.workspace['id']}"
    missing = f"/api/v1/workspaces/{uuid.uuid4()}"
    for role in ("non_member", "admin"):
        client = roles.client(role)
        for method, body in (("GET", None), ("PATCH", {"version": 1, "name": "Taken over"})):
            hidden = client.request(method, real, json=body)
            absent = client.request(method, missing, json=body)

            assert hidden.status_code == absent.status_code == 404, (role, method)
            assert hidden.json() == absent.json()

    assert roles.client("owner").get(real).json()["name"] == "Permission Matrix"
