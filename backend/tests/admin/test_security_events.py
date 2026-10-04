"""The security-event log (spec story 23): admins review it, newest first, filtered by
event type, actor and date, a page at a time."""

from __future__ import annotations

from datetime import timedelta

from dawam.platform.clock import FakeClock
from tests.helpers import sign_in

EVENTS = "/api/v1/admin/security-events"


def _types(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [event["event_type"] for event in response.json()["items"]]


def test_an_admin_reviews_security_events_newest_first(
    admin_client, admin_user, create_user, anonymous_client, clock: FakeClock
):
    grace = create_user(email="grace@example.com")
    clock.advance(timedelta(minutes=1))
    sign_in(anonymous_client, grace.email, "not the password")

    response = admin_client.get(EVENTS)

    assert _types(response) == ["login_failed", "login_succeeded"]  # the admin's own sign-in
    newest = response.json()["items"][0]
    assert newest == {
        "id": newest["id"],
        "event_type": "login_failed",
        "actor_id": None,
        "actor_email": None,
        "target_type": "user",
        "target_id": str(grace.id),
        "metadata": {"email": "grace@example.com", "reason": "invalid_credentials"},
        "ip": "testclient",
        "created_at": "2026-01-05T09:01:00Z",
    }
    oldest = response.json()["items"][1]
    assert (oldest["actor_id"], oldest["actor_email"]) == (str(admin_user.id), admin_user.email)


def test_security_events_filter_by_event_type(admin_client, create_user, anonymous_client):
    grace = create_user(email="grace@example.com")
    sign_in(anonymous_client, grace.email, "not the password")

    assert _types(admin_client.get(EVENTS, params={"event_type": "login_failed"})) == [
        "login_failed"
    ]


def test_security_events_filter_by_actor_email(admin_client, create_user, anonymous_client):
    grace = create_user(email="grace@example.com")
    sign_in(anonymous_client, grace.email, grace.password)

    response = admin_client.get(EVENTS, params={"actor": " Grace@Example.com "})

    assert _types(response) == ["login_succeeded"]
    assert response.json()["items"][0]["actor_email"] == "grace@example.com"


def test_security_events_filter_by_date(
    admin_client, admin_user, create_user, anonymous_client, clock: FakeClock
):
    grace = create_user(email="grace@example.com")
    clock.advance(timedelta(days=2))
    sign_in(anonymous_client, grace.email, "wrong on day two")
    clock.advance(timedelta(days=2))
    sign_in(anonymous_client, grace.email, "wrong on day four")
    sign_in(admin_client, admin_user.email, admin_user.password)  # the session idled out

    response = admin_client.get(
        EVENTS, params={"since": "2026-01-06T00:00:00Z", "until": "2026-01-08T00:00:00Z"}
    )

    assert _types(response) == ["login_failed"]
    assert response.json()["items"][0]["created_at"] == "2026-01-07T09:00:00Z"


def test_security_events_are_cursor_paginated(admin_client, create_user, anonymous_client):
    grace = create_user(email="grace@example.com")
    for _ in range(3):
        sign_in(anonymous_client, grace.email, "not the password")

    first = admin_client.get(EVENTS, params={"event_type": "login_failed", "limit": 2})
    assert len(_types(first)) == 2
    second = admin_client.get(
        EVENTS,
        params={"event_type": "login_failed", "limit": 2, "cursor": first.json()["next_cursor"]},
    )

    assert len(_types(second)) == 1
    assert second.json()["next_cursor"] is None
    ids = [e["id"] for e in first.json()["items"] + second.json()["items"]]
    assert len(set(ids)) == 3


def test_regular_users_cannot_see_security_events(signed_in_client):
    response = signed_in_client.get(EVENTS)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_dates_without_a_time_zone_are_read_as_utc(admin_client, create_user, anonymous_client):
    grace = create_user(email="grace@example.com")
    sign_in(anonymous_client, grace.email, "not the password")  # at 09:00 UTC

    after = admin_client.get(EVENTS, params={"since": "2026-01-05T09:30:00"})
    before = admin_client.get(EVENTS, params={"until": "2026-01-05T09:30:00"})

    assert _types(after) == []
    assert "login_failed" in _types(before)
