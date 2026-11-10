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


def test_the_list_is_newest_first_and_marking_read_keeps_it_with_read_at(
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
    assert [(n["message"], n["read_at"] is not None) for n in after["items"]] == [
        ("second", True),
        ("first", False),
    ]
    assert after["unread_count"] == 1


def test_read_all_marks_everything_read_but_keeps_it_listed(
    roles: RoleClients, notifications: NotificationService
):
    owner = roles.user("owner")
    notifications.notify([owner.id], kind="job", message="a")
    notifications.notify([owner.id], kind="job", message="b")
    client = roles.client("owner")

    assert client.post("/api/v1/notifications/read-all").status_code == 204

    listed = client.get("/api/v1/notifications").json()
    assert listed["unread_count"] == 0
    assert len(listed["items"]) == 2
    assert all(n["read_at"] is not None for n in listed["items"])
    assert listed["next_cursor"] is None


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


def _page(client, **params) -> dict:
    response = client.get("/api/v1/notifications", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_pages_follow_the_cursor_newest_first_and_the_last_has_none(
    roles: RoleClients, notifications: NotificationService, clock
):
    owner = roles.user("owner")
    for n in range(5):
        notifications.notify([owner.id], kind="job", message=f"n{n}")
        clock.advance(timedelta(seconds=1))
    client = roles.client("owner")

    first = _page(client, limit=2)
    second = _page(client, limit=2, cursor=first["next_cursor"])
    third = _page(client, limit=2, cursor=second["next_cursor"])

    assert [n["message"] for n in first["items"]] == ["n4", "n3"]
    assert [n["message"] for n in second["items"]] == ["n2", "n1"]
    assert [n["message"] for n in third["items"]] == ["n0"]
    assert third["next_cursor"] is None
    assert first["unread_count"] == second["unread_count"] == 5


def test_a_cursor_never_skips_or_repeats_rows_sharing_a_created_at(
    roles: RoleClients, notifications: NotificationService
):
    owner = roles.user("owner")
    for n in range(5):  # the clock does not move: one created_at for all
        notifications.notify([owner.id], kind="job", message=f"n{n}")
    client = roles.client("owner")

    seen: list[str] = []
    cursor = None
    while True:
        page = _page(client, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [n["id"] for n in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5 and len(set(seen)) == 5


def test_the_default_page_is_twenty_and_the_limit_is_capped_at_fifty(
    roles: RoleClients, notifications: NotificationService
):
    owner = roles.user("owner")
    for n in range(25):
        notifications.notify([owner.id], kind="job", message=f"n{n}")
    client = roles.client("owner")

    assert len(_page(client)["items"]) == 20
    assert client.get("/api/v1/notifications", params={"limit": 51}).status_code == 422
    assert client.get("/api/v1/notifications", params={"limit": 0}).status_code == 422


def test_paging_never_shows_another_users_notifications(
    roles: RoleClients, notifications: NotificationService
):
    owner, viewer = roles.user("owner"), roles.user("viewer")
    notifications.notify([owner.id], kind="job", message="mine")
    notifications.notify([viewer.id], kind="job", message="theirs")

    page = _page(roles.client("owner"), limit=1)

    assert [n["message"] for n in page["items"]] == ["mine"]
    assert page["next_cursor"] is None
    assert page["unread_count"] == 1


def test_an_invalid_cursor_is_refused(roles: RoleClients):
    client = roles.client("owner")

    response = client.get("/api/v1/notifications", params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
