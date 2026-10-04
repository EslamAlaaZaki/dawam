"""The Workspace activity feed (spec story 38): who changed what and when, newest first,
readable by every member, written by the Workspace and membership operations."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from tests.roles import RoleClients


def feed_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/activity"


def feed(roles: RoleClients, as_role="owner", **params) -> dict:
    response = roles.client(as_role).get(feed_path(roles), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def summary(page: dict) -> list[tuple[str, str, str]]:
    """(actor, verb, object label) of each item, newest first."""
    return [
        (i["actor"]["display_name"] if i["actor"] else "", i["verb"], i["object_label"])
        for i in page["items"]
    ]


@pytest.fixture
def colleague(roles: RoleClients, auth_service):
    return auth_service.create_user(
        email="colleague@example.com",
        password="colleague password",
        display_name="Colleague",
        system_role="user",
    )


def test_creating_a_workspace_is_the_first_event(roles: RoleClients):
    page = feed(roles)

    assert summary(page) == [("Owner", "workspace.created", "Permission Matrix")]
    assert page["items"][0]["object_type"] == "workspace"
    assert page["items"][0]["object_id"] == str(roles.workspace_id)
    assert page["next_cursor"] is None


def step(clock) -> None:
    """Events at the same instant have no defined order, so move time on."""
    clock.advance(timedelta(minutes=1))


def test_workspace_edits_and_member_changes_are_recorded_newest_first(
    roles: RoleClients, colleague, clock
):
    roles.workspace  # noqa: B018 - created now
    step(clock)
    roles.client("viewer")
    step(clock)
    roles.client("editor")
    step(clock)
    owner = roles.client("owner")
    base = f"/api/v1/workspaces/{roles.workspace_id}"
    version = roles.workspace["version"]
    assert owner.patch(base, json={"version": version, "name": "Renamed"}).status_code == 200
    step(clock)
    # Nothing changes, so nothing is recorded.
    assert owner.patch(base, json={"version": version + 1, "name": "Renamed"}).status_code == 200
    step(clock)
    added = owner.post(f"{base}/members", json={"email": colleague.email, "role": "viewer"})
    assert added.status_code == 201
    step(clock)
    changed = owner.patch(f"{base}/members/{colleague.id}", json={"role": "editor"})
    assert changed.status_code == 200
    step(clock)
    assert owner.delete(f"{base}/members/{colleague.id}").status_code == 204
    step(clock)
    assert roles.client("viewer").post(f"{base}/leave").status_code == 204
    step(clock)
    editor_id = str(roles.user("editor").id)
    transferred = owner.post(f"{base}/transfer-ownership", json={"user_id": editor_id})
    assert transferred.status_code == 200, transferred.text

    page = feed(roles, as_role="editor")

    assert summary(page) == [
        ("Owner", "workspace.ownership_transferred", "Editor"),
        ("Viewer", "member.left", "Viewer"),
        ("Owner", "member.removed", "Colleague"),
        ("Owner", "member.role_changed", "Colleague"),
        ("Owner", "member.added", "Colleague"),
        ("Owner", "workspace.updated", "Renamed"),
        ("Owner", "member.added", "Editor"),
        ("Owner", "member.added", "Viewer"),
        ("Owner", "workspace.created", "Permission Matrix"),
    ]
    by_verb = {i["verb"]: i for i in page["items"]}
    assert by_verb["workspace.updated"]["details"] == {"changed": ["name"]}
    assert by_verb["member.role_changed"]["details"] == {"from": "viewer", "to": "editor"}
    assert by_verb["member.removed"]["details"] == {"role": "editor"}
    assert by_verb["member.left"]["actor"]["user_id"] == str(roles.user("viewer").id)


def test_viewers_read_the_feed_and_non_members_cannot(roles: RoleClients):
    assert feed(roles, as_role="viewer")["items"]
    assert roles.client("non_member").get(feed_path(roles)).status_code == 404


def test_the_feed_is_paged_with_a_cursor(roles: RoleClients, app, clock):
    roles.workspace  # noqa: B018 - created now
    for n in range(4):
        step(clock)
        with Session(app.state.engine) as db, db.begin():
            record_activity(
                db,
                workspace_id=roles.workspace_id,
                actor_id=roles.user("owner").id,
                verb="thing.happened",
                object_type="thing",
                object_label=f"Thing {n}",
                at=clock(),
            )

    first = feed(roles, limit=2)
    second = feed(roles, limit=2, cursor=first["next_cursor"])
    third = feed(roles, limit=2, cursor=second["next_cursor"])

    labels = [i["object_label"] for p in (first, second, third) for i in p["items"]]
    assert labels == ["Thing 3", "Thing 2", "Thing 1", "Thing 0", "Permission Matrix"]
    assert third["next_cursor"] is None
    assert first["next_cursor"] and second["next_cursor"]


def test_a_cursor_that_was_not_issued_is_refused(roles: RoleClients):
    response = roles.client("owner").get(feed_path(roles), params={"cursor": "garbage"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


def test_a_feed_shows_only_its_own_workspace(roles: RoleClients):
    other = roles.client("non_member").post("/api/v1/workspaces", json={"name": "Other"})
    assert other.status_code == 201

    assert [i["object_label"] for i in feed(roles)["items"]] == ["Permission Matrix"]
