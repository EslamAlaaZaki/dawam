"""Admins and Workspaces (spec §4.3, stories 21, 22): the list of every Workspace's
metadata, never its content, and rescuing a Workspace whose owners are all deactivated."""

from __future__ import annotations

import pytest

from dawam.modules.auth import SecurityEventRecorder, UserAdministration
from dawam.modules.workspaces import WorkspaceService
from tests.roles import RoleClients

LIST = "/api/v1/admin/workspaces"


def reassign_path(workspace_id) -> str:
    return f"{LIST}/{workspace_id}/reassign-owner"


def error_code(response) -> str:
    return response.json()["error"]["code"]


def deactivate(app, user_id, actor_id, clock) -> None:
    UserAdministration(app.state.engine, clock=clock).update_user(
        user_id, is_active=False, actor_id=actor_id
    )


@pytest.fixture
def orphaned(app, roles: RoleClients, clock):
    """The Workspace of ``roles`` with an editor and a viewer, after its only owner was
    deactivated."""
    roles.client("editor")
    roles.client("viewer")
    deactivate(app, roles.user("owner").id, roles.user("admin").id, clock)
    return roles.workspace_id


# The list


def test_the_admin_list_shows_metadata_of_every_workspace_and_never_content(
    roles: RoleClients, auth_service, app, clock
):
    roles.client("editor")
    other = auth_service.create_user(
        email="solo@example.com", password="solo password", display_name="Solo", system_role="user"
    )
    WorkspaceService(app.state.engine, clock=clock).create(
        other, name="Another", description="secret plans", domain="Retail"
    )

    response = roles.client("admin").get(LIST)

    assert response.status_code == 200
    by_name = {w["name"]: w for w in response.json()["items"]}
    assert list(by_name) == ["Another", "Permission Matrix"]
    matrix = by_name["Permission Matrix"]
    assert matrix["member_count"] == 2
    assert [o["email"] for o in matrix["owners"]] == [roles.user("owner").email]
    assert matrix["has_active_owner"] is True
    assert matrix["status"] == "active"
    assert matrix["created_at"] and matrix["updated_at"]
    assert set(matrix) == {
        "id",
        "name",
        "status",
        "owners",
        "member_count",
        "has_active_owner",
        "created_at",
        "updated_at",
        "archived_at",
    }
    assert "secret plans" not in response.text and "Retail" not in response.text


def test_the_admin_list_is_paged_by_name(roles: RoleClients, auth_service, app, clock):
    service = WorkspaceService(app.state.engine, clock=clock)
    assert roles.workspace["name"] == "Permission Matrix"
    for name in ("Alpha", "Beta"):
        service.create(roles.user("owner"), name=name)

    first = roles.client("admin").get(LIST, params={"limit": 2}).json()
    second = roles.client("admin").get(LIST, params={"limit": 2, "cursor": first["next_cursor"]})

    assert [w["name"] for w in first["items"]] == ["Alpha", "Beta"]
    assert second.status_code == 200, second.text
    assert [w["name"] for w in second.json()["items"]] == ["Permission Matrix"]
    assert second.json()["next_cursor"] is None


def test_the_admin_list_marks_archived_and_orphaned_workspaces(
    roles: RoleClients, orphaned, app, clock
):
    WorkspaceService(app.state.engine, clock=clock).archive(roles.user("admin"), orphaned)

    (workspace,) = roles.client("admin").get(LIST).json()["items"]

    assert workspace["status"] == "archived"
    assert workspace["archived_at"] is not None
    assert workspace["has_active_owner"] is False
    assert workspace["owners"][0]["is_active"] is False


@pytest.mark.parametrize("role", ["non_member", "viewer", "editor", "owner"])
def test_other_users_cannot_list_every_workspace(roles: RoleClients, role):
    assert roles.client(role).get(LIST).status_code == 403


# Reassigning ownership


def test_an_admin_reassigns_an_orphaned_workspace_to_an_active_user(
    roles: RoleClients, orphaned, app
):
    editor = roles.user("editor")

    response = roles.client("admin").post(reassign_path(orphaned), json={"user_id": str(editor.id)})

    assert response.status_code == 200, response.text
    assert response.json()["has_active_owner"] is True
    active = [o["email"] for o in response.json()["owners"] if o["is_active"]]
    assert active == [editor.email]
    mine = roles.client("editor").get(f"/api/v1/workspaces/{orphaned}").json()
    assert mine["role"] == "owner"


def test_an_admin_may_pick_someone_who_is_not_a_member_yet(roles: RoleClients, orphaned):
    newcomer = roles.user("non_member")

    response = roles.client("admin").post(
        reassign_path(orphaned), json={"user_id": str(newcomer.id)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["member_count"] == 4
    mine = roles.client("non_member").get(f"/api/v1/workspaces/{orphaned}").json()
    assert mine["role"] == "owner"


def test_reassignment_is_a_security_event_and_notifies_the_remaining_members(
    roles: RoleClients, orphaned, app
):
    editor = roles.user("editor")
    roles.client("admin").post(reassign_path(orphaned), json={"user_id": str(editor.id)})

    (event,) = [
        e
        for e in SecurityEventRecorder(app.state.engine, clock=app.state.services.clock).recent()
        if e.event_type == "workspace_ownership_reassigned"
    ]
    assert event.actor_id == roles.user("admin").id
    assert event.target_id == orphaned
    assert event.metadata["new_owner_id"] == str(editor.id)
    assert event.metadata["previous_role"] == "editor"
    feed = roles.client("editor").get(f"/api/v1/workspaces/{orphaned}/activity").json()["items"]
    assert [i["object_id"] for i in feed if i["verb"] == "workspace.ownership_reassigned"] == [
        str(editor.id)
    ]
    for role in ("editor", "viewer"):
        unread = roles.client(role).get("/api/v1/notifications").json()
        assert unread["unread_count"] == 1
        (note,) = unread["items"]
        assert note["kind"] == "ownership"
        assert note["workspace_id"] == str(orphaned)
        assert "Permission Matrix" in note["message"]
    assert roles.client("non_member").get("/api/v1/notifications").json()["items"] == []


def test_reassignment_is_refused_while_an_owner_is_active(roles: RoleClients, app):
    roles.client("editor")
    workspace_id = roles.workspace_id

    response = roles.client("admin").post(
        reassign_path(workspace_id), json={"user_id": str(roles.user("editor").id)}
    )

    assert response.status_code == 409
    assert error_code(response) == "has_active_owner"
    assert roles.client("editor").get(f"/api/v1/workspaces/{workspace_id}").json()["role"] == (
        "editor"
    )
    assert roles.client("owner").get(f"/api/v1/workspaces/{workspace_id}").json()["role"] == (
        "owner"
    )
    assert roles.client("editor").get("/api/v1/notifications").json()["items"] == []


def test_one_active_owner_among_deactivated_ones_is_enough_to_refuse(
    roles: RoleClients, app, clock
):
    editor = roles.user("editor")
    roles.client("editor")
    # The editor becomes a second owner; the first one is deactivated, the second stays.
    promoted = roles.client("owner").patch(
        f"/api/v1/workspaces/{roles.workspace_id}/members/{editor.id}", json={"role": "owner"}
    )
    assert promoted.status_code == 200
    deactivate(app, roles.user("owner").id, roles.user("admin").id, clock)

    response = roles.client("admin").post(
        reassign_path(roles.workspace_id), json={"user_id": str(roles.user("viewer").id)}
    )

    assert response.status_code == 409
    assert error_code(response) == "has_active_owner"


def test_the_new_owner_must_be_an_active_user(roles: RoleClients, orphaned, app, clock):
    viewer = roles.user("viewer")
    deactivate(app, viewer.id, roles.user("admin").id, clock)

    inactive = roles.client("admin").post(reassign_path(orphaned), json={"user_id": str(viewer.id)})
    unknown = roles.client("admin").post(
        reassign_path(orphaned), json={"user_id": "00000000-0000-4000-8000-000000000000"}
    )

    assert inactive.status_code == 422
    assert error_code(inactive) == "user_inactive"
    assert unknown.status_code == 404
    assert error_code(unknown) == "user_not_found"


def test_reassigning_an_unknown_workspace_is_404(roles: RoleClients):
    response = roles.client("admin").post(
        reassign_path("00000000-0000-4000-8000-000000000000"),
        json={"user_id": str(roles.user("owner").id)},
    )

    assert response.status_code == 404


def test_a_deactivated_owners_workspace_can_be_archived_and_deleted_by_the_admin(
    roles: RoleClients, orphaned
):
    """Even without reassigning, an admin can archive and then delete an abandoned
    Workspace, without ever opening it."""
    admin = roles.client("admin")
    path = f"/api/v1/workspaces/{orphaned}"

    assert admin.post(f"{path}/archive").status_code == 204
    assert admin.get(path).status_code == 404
    assert admin.request("DELETE", path, json={"name": "Permission Matrix"}).status_code == 204
    assert admin.get(LIST).json()["items"] == []
