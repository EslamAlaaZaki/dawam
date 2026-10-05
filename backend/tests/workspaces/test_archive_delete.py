"""Archiving, unarchiving and deleting a Workspace (spec §4.3, stories 35, 36).

An archived Workspace is read-only through the policy itself: ``can`` refuses every
action that changes anything, so modules built later inherit it. Reads still work.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from dawam.modules.auth import SecurityEventRecorder
from dawam.modules.workspaces import WORKSPACE_ACTIONS, Action, WorkspaceScope, can
from tests.roles import RoleClients


def path(roles: RoleClients, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}{suffix}"


def archive(roles: RoleClients, as_role="owner"):
    return roles.client(as_role).post(path(roles, "/archive"))


def unarchive(roles: RoleClients, as_role="owner"):
    return roles.client(as_role).post(path(roles, "/unarchive"))


def delete(roles: RoleClients, as_role="owner", name: str | None = None):
    return roles.client(as_role).request(
        "DELETE", path(roles), json={"name": roles.workspace["name"] if name is None else name}
    )


def error_code(response) -> str:
    return response.json()["error"]["code"]


def events(app, event_type: str):
    recorder = SecurityEventRecorder(app.state.engine, clock=app.state.services.clock)
    return [e for e in recorder.recent() if e.event_type == event_type]


# Archive and unarchive


def test_an_owner_archives_and_unarchives(roles: RoleClients):
    assert archive(roles).status_code == 204

    archived = roles.client("owner").get(path(roles)).json()
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None

    assert unarchive(roles).status_code == 204

    active = roles.client("owner").get(path(roles)).json()
    assert active["status"] == "active"
    assert active["archived_at"] is None
    assert active["version"] == roles.workspace["version"] + 2


def test_an_admin_who_is_not_a_member_archives_and_unarchives_but_still_sees_no_content(
    roles: RoleClients,
):
    assert archive(roles, "admin").status_code == 204
    assert roles.client("admin").get(path(roles)).status_code == 404
    assert roles.client("admin").get(path(roles, "/members")).status_code == 404

    assert unarchive(roles, "admin").status_code == 204
    assert roles.client("admin").get(path(roles)).status_code == 404


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_editors_and_viewers_cannot_archive(roles: RoleClients, role):
    assert archive(roles, role).status_code == 403
    assert roles.client("owner").get(path(roles)).json()["status"] == "active"


def test_unarchive_needs_an_archived_workspace_and_archive_an_active_one(roles: RoleClients):
    assert unarchive(roles).status_code == 409
    assert error_code(unarchive(roles)) == "workspace_not_archived"

    assert archive(roles).status_code == 204
    again = archive(roles)

    assert again.status_code == 409
    assert error_code(again) == "workspace_archived"


# Read-only


def test_an_archived_workspace_refuses_every_change(roles: RoleClients, colleague_email: str):
    roles.client("editor")
    assert archive(roles).status_code == 204
    owner = roles.client("owner")

    renamed = owner.patch(path(roles), json={"version": 1, "name": "Renamed"})
    added = owner.post(path(roles, "/members"), json={"email": colleague_email, "role": "viewer"})
    promoted = owner.patch(
        path(roles, f"/members/{roles.user('editor').id}"), json={"role": "owner"}
    )
    removed = owner.delete(path(roles, f"/members/{roles.user('editor').id}"))
    transferred = owner.post(
        path(roles, "/transfer-ownership"), json={"user_id": str(roles.user("editor").id)}
    )

    for refused in (renamed, added, promoted, removed, transferred):
        assert refused.status_code == 409, refused.text
        assert error_code(refused) == "workspace_archived"
    assert owner.get(path(roles)).json()["name"] == roles.workspace["name"]


def test_reads_still_work_while_archived_and_the_permissions_say_read_only(roles: RoleClients):
    roles.client("viewer")
    assert archive(roles).status_code == 204

    for role in ("owner", "viewer"):
        client = roles.client(role)
        assert client.get(path(roles)).status_code == 200
        assert client.get(path(roles, "/progress")).status_code == 200
        assert client.get(path(roles, "/members")).status_code == 200
    permissions = set(roles.client("owner").get(path(roles)).json()["permissions"])
    assert permissions == {
        "workspace.view",
        "workspace.unarchive",
        "workspace.delete",
        "workspace.leave",
    }


def test_a_member_can_still_leave_an_archived_workspace(roles: RoleClients):
    roles.client("editor")
    assert archive(roles).status_code == 204

    assert roles.client("editor").post(path(roles, "/leave")).status_code == 204


def test_the_policy_refuses_every_action_that_changes_anything_once_archived(
    roles: RoleClients,
):
    """The guarantee modules built later rely on: only the actions listed here survive
    archiving, so a new action is read-only when archived unless someone opts it in."""
    survive = {
        Action.VIEW_WORKSPACE,
        Action.LEAVE_WORKSPACE,
        Action.UNARCHIVE_WORKSPACE,
        Action.DELETE_WORKSPACE,
    }
    owner, admin = roles.user("owner"), roles.user("admin")
    workspace_id = uuid.uuid4()
    as_owner = WorkspaceScope(workspace_id, owner.id, "owner", archived=True)
    as_admin = WorkspaceScope(workspace_id, admin.id, None, archived=True)

    for action in WORKSPACE_ACTIONS:
        assert can(owner, action, as_owner) == (action in survive), action
    assert {a for a in WORKSPACE_ACTIONS if can(admin, a, as_admin)} == {
        Action.UNARCHIVE_WORKSPACE,
        Action.DELETE_WORKSPACE,
        Action.REASSIGN_OWNERSHIP,
    }


def test_archiving_applies_to_active_workspaces_and_unarchiving_to_archived_ones(
    roles: RoleClients,
):
    owner = roles.user("owner")
    active = WorkspaceScope(uuid.uuid4(), owner.id, "owner", archived=False)

    assert can(owner, Action.ARCHIVE_WORKSPACE, active)
    assert not can(owner, Action.UNARCHIVE_WORKSPACE, active)


def test_archiving_and_unarchiving_are_in_the_activity_feed(roles: RoleClients, clock):
    assert roles.workspace
    clock.advance(timedelta(minutes=1))
    assert archive(roles, "admin").status_code == 204
    clock.advance(timedelta(minutes=1))
    assert unarchive(roles).status_code == 204

    items = roles.client("owner").get(path(roles, "/activity")).json()["items"]

    assert [(i["verb"], i["actor"]["display_name"]) for i in items[:2]] == [
        ("workspace.unarchived", "Owner"),
        ("workspace.archived", "Admin"),
    ]
    assert items[0]["object_label"] == "Permission Matrix"


# Delete


def test_deleting_needs_the_name_typed_exactly(roles: RoleClients):
    for wrong in ("", "permission matrix", "Permission Matrix ", "Other"):
        refused = delete(roles, name=wrong)
        assert refused.status_code == 422, wrong
        assert error_code(refused) == "name_mismatch"
    assert roles.client("owner").get(path(roles)).status_code == 200


def test_an_owner_deletes_the_workspace_with_its_members_and_it_is_recorded(
    app, roles: RoleClients
):
    roles.client("editor")

    assert delete(roles).status_code == 204

    assert roles.client("owner").get(path(roles)).status_code == 404
    assert roles.client("editor").get(path(roles)).status_code == 404
    with app.state.engine.connect() as conn:
        left = conn.scalar(
            sa.text("SELECT count(*) FROM workspace_members WHERE workspace_id = :w"),
            {"w": roles.workspace_id},
        )
    assert left == 0
    (event,) = events(app, "workspace_deleted")
    assert event.actor_id == roles.user("owner").id
    assert event.target_id == roles.workspace_id
    assert event.metadata == {
        "name": "Permission Matrix",
        "was_archived": False,
        "as_member": True,
    }


def test_an_owner_deletes_an_archived_workspace_too(roles: RoleClients):
    assert archive(roles).status_code == 204

    assert delete(roles).status_code == 204


def test_a_non_member_admin_deletes_only_an_archived_workspace(app, roles: RoleClients):
    refused = delete(roles, "admin")
    assert refused.status_code == 409
    assert error_code(refused) == "workspace_not_archived"
    assert roles.client("owner").get(path(roles)).status_code == 200

    assert archive(roles).status_code == 204
    assert delete(roles, "admin", name="wrong").status_code == 422
    assert delete(roles, "admin").status_code == 204

    (event,) = events(app, "workspace_deleted")
    assert event.actor_id == roles.user("admin").id
    assert event.metadata["was_archived"] is True
    assert event.metadata["as_member"] is False


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_editors_and_viewers_cannot_delete(roles: RoleClients, role):
    assert delete(roles, role).status_code == 403


def test_a_non_member_cannot_delete_or_learn_the_workspace_exists(roles: RoleClients):
    assert delete(roles, "non_member").status_code == 404
    assert delete(roles, "non_member", name="wrong").status_code == 404


@pytest.fixture
def colleague_email(auth_service) -> str:
    auth_service.create_user(
        email="newbie@example.com",
        password="newbie password",
        display_name="Newbie",
        system_role="user",
    )
    return "newbie@example.com"
