"""Notifications: a row per recipient and each user's unread list."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from dawam.modules.notifications import NotificationService
from tests.roles import RoleClients


@pytest.fixture
def notifications(app, clock) -> NotificationService:
    return NotificationService(app.state.engine, clock=clock)


def test_each_recipient_gets_one_unread_notification(
    roles: RoleClients, notifications: NotificationService
):
    owner, viewer = roles.user("owner"), roles.user("viewer")
    roles.client("owner")
    roles.client("viewer")

    created = notifications.notify(
        [owner.id, viewer.id, owner.id], kind="job", message="Export finished."
    )

    assert created == 2
    for role in ("owner", "viewer"):
        listed = roles.client(role).get("/api/v1/notifications").json()
        assert listed["unread_count"] == 1
        assert [(n["kind"], n["message"]) for n in listed["items"]] == [("job", "Export finished.")]


def test_an_unknown_kind_is_refused(roles: RoleClients, notifications: NotificationService):
    with pytest.raises(ValueError, match="kind"):
        notifications.notify(
            [roles.user("owner").id],
            kind="gossip",  # type: ignore[arg-type]
            message="x",
        )


def test_the_list_is_newest_first_and_marking_read_removes_one(
    roles: RoleClients, notifications: NotificationService, clock
):
    owner = roles.user("owner")
    notifications.notify([owner.id], kind="job", message="first")
    clock.advance(timedelta(seconds=5))
    notifications.notify([owner.id], kind="job", message="second")
    client = roles.client("owner")

    items = client.get("/api/v1/notifications").json()["items"]
    assert [n["message"] for n in items] == ["second", "first"]
    assert client.post(f"/api/v1/notifications/{items[0]['id']}/read").status_code == 204

    after = client.get("/api/v1/notifications").json()
    assert [n["message"] for n in after["items"]] == ["first"]
    assert after["unread_count"] == 1


def test_read_all_clears_the_list(roles: RoleClients, notifications: NotificationService):
    owner = roles.user("owner")
    notifications.notify([owner.id], kind="job", message="a")
    notifications.notify([owner.id], kind="job", message="b")
    client = roles.client("owner")

    assert client.post("/api/v1/notifications/read-all").status_code == 204

    assert client.get("/api/v1/notifications").json() == {"items": [], "unread_count": 0}


def test_nobody_reads_or_clears_someone_elses_notifications(
    roles: RoleClients, notifications: NotificationService
):
    owner = roles.user("owner")
    notifications.notify([owner.id], kind="job", message="private")
    (mine,) = roles.client("owner").get("/api/v1/notifications").json()["items"]

    assert roles.client("viewer").get("/api/v1/notifications").json()["items"] == []
    other = roles.client("viewer")
    assert other.post(f"/api/v1/notifications/{mine['id']}/read").status_code == 204
    assert roles.client("viewer").post("/api/v1/notifications/read-all").status_code == 204
    assert (
        roles.client("owner").post(f"/api/v1/notifications/{uuid.uuid4()}/read").status_code == 204
    )

    assert roles.client("owner").get("/api/v1/notifications").json()["unread_count"] == 1


def test_anonymous_users_have_no_notifications(anonymous_client):
    assert anonymous_client.get("/api/v1/notifications").status_code == 401
