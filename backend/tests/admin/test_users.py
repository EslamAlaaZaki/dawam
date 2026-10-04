"""Admin user management (spec stories 14, 16-19; §6.2): list, search and filter
users, deactivate and reactivate them, promote and demote admins, and force a
password reset."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi import FastAPI

from dawam.modules.auth import SecurityEventRecorder
from dawam.modules.mail import MailService
from dawam.platform.clock import FakeClock
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import InMemoryOutbox
from tests.helpers import csrf_token, sign_in


def test_an_admin_lists_every_user_by_email(admin_client, admin_user, create_user):
    grace = create_user(email="grace@example.com", display_name="Grace Hopper")

    response = admin_client.get("/api/v1/admin/users")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [user["email"] for user in body["items"]] == ["grace@example.com", "root@example.com"]
    assert body["items"][0] == {
        "id": str(grace.id),
        "email": "grace@example.com",
        "display_name": "Grace Hopper",
        "system_role": "user",
        "is_active": True,
        "must_change_password": False,
        "created_at": "2026-01-05T09:00:00Z",
        "last_login_at": None,
    }
    assert body["next_cursor"] is None


def _emails(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [user["email"] for user in response.json()["items"]]


def test_an_admin_searches_users_by_email_or_display_name_ignoring_case(admin_client, create_user):
    create_user(email="grace@navy.example", display_name="Grace Hopper")
    create_user(email="ada@example.com", display_name="Ada Lovelace")
    create_user(email="alan@example.com", display_name="Alan Turing")

    assert _emails(admin_client.get("/api/v1/admin/users", params={"q": "NAVY"})) == [
        "grace@navy.example"
    ]
    assert _emails(admin_client.get("/api/v1/admin/users", params={"q": "lovelace"})) == [
        "ada@example.com"
    ]


def test_search_text_is_matched_literally(admin_client, create_user):
    create_user(email="grace@example.com", display_name="Grace 100% Hopper")
    create_user(email="ada@example.com", display_name="Ada Lovelace")

    assert _emails(admin_client.get("/api/v1/admin/users", params={"q": "0%"})) == [
        "grace@example.com"
    ]
    assert _emails(admin_client.get("/api/v1/admin/users", params={"q": "_"})) == []


def test_an_admin_filters_users_by_system_role(admin_client, create_user):
    create_user(email="grace@example.com")
    create_user(email="second-admin@example.com", system_role="admin")

    assert _emails(admin_client.get("/api/v1/admin/users", params={"role": "admin"})) == [
        "root@example.com",
        "second-admin@example.com",
    ]
    assert _emails(admin_client.get("/api/v1/admin/users", params={"role": "user"})) == [
        "grace@example.com"
    ]


def test_the_user_list_is_cursor_paginated_and_keeps_its_filters(admin_client, create_user):
    for name in ("a", "b", "c", "d"):
        create_user(email=f"{name}@example.com", display_name=f"Person {name}")

    first = admin_client.get("/api/v1/admin/users", params={"q": "person", "limit": 3})
    assert _emails(first) == ["a@example.com", "b@example.com", "c@example.com"]
    cursor = first.json()["next_cursor"]

    second = admin_client.get(
        "/api/v1/admin/users", params={"q": "person", "limit": 3, "cursor": cursor}
    )
    assert _emails(second) == ["d@example.com"]
    assert second.json()["next_cursor"] is None


@pytest.fixture
def events(app: FastAPI, admin_client, clock: FakeClock) -> SecurityEventRecorder:
    return SecurityEventRecorder(app.state.engine, clock=clock)


def _patch(client, user_id, **changes):
    return client.patch(f"/api/v1/admin/users/{user_id}", json=changes)


def test_deactivating_a_user_ends_their_sessions_at_once_and_blocks_sign_in(
    admin_client, signed_in_client, signed_in_user, anonymous_client
):
    response = _patch(admin_client, signed_in_user.id, is_active=False)

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    assert signed_in_client.get("/api/v1/me").status_code == 401
    refused = sign_in(anonymous_client, signed_in_user.email, signed_in_user.password)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "account_deactivated"


def test_a_deactivated_user_with_a_wrong_password_learns_nothing_more(
    admin_client, signed_in_user, anonymous_client
):
    _patch(admin_client, signed_in_user.id, is_active=False)

    refused = sign_in(anonymous_client, signed_in_user.email, "not the password")

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "invalid_credentials"


def test_reactivating_a_user_restores_access(admin_client, signed_in_user, anonymous_client):
    _patch(admin_client, signed_in_user.id, is_active=False)

    response = _patch(admin_client, signed_in_user.id, is_active=True)

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True
    assert (
        sign_in(anonymous_client, signed_in_user.email, signed_in_user.password).status_code == 200
    )


def test_the_user_list_filters_by_active_status(admin_client, create_user):
    grace = create_user(email="grace@example.com")
    create_user(email="ada@example.com")
    _patch(admin_client, grace.id, is_active=False)

    assert _emails(admin_client.get("/api/v1/admin/users", params={"active": "false"})) == [
        "grace@example.com"
    ]
    assert _emails(admin_client.get("/api/v1/admin/users", params={"active": "true"})) == [
        "ada@example.com",
        "root@example.com",
    ]


def test_deactivation_and_reactivation_are_security_events(
    admin_client, admin_user, signed_in_client, signed_in_user, events: SecurityEventRecorder
):
    _patch(admin_client, signed_in_user.id, is_active=False)
    _patch(admin_client, signed_in_user.id, is_active=True)

    reactivated, deactivated = events.recent(limit=2)
    assert (deactivated.event_type, reactivated.event_type) == (
        "user_deactivated",
        "user_reactivated",
    )
    for event in (deactivated, reactivated):
        assert event.actor_id == admin_user.id
        assert (event.target_type, event.target_id) == ("user", signed_in_user.id)
        assert event.ip == "testclient"
    assert deactivated.metadata == {"email": signed_in_user.email, "sessions_ended": 1}


def test_changing_an_unknown_user_is_not_found(admin_client):
    response = _patch(admin_client, uuid.uuid4(), is_active=False)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_admin_promotes_a_user_and_demotes_them_again(
    admin_client, admin_user, signed_in_user, events: SecurityEventRecorder
):
    promoted = _patch(admin_client, signed_in_user.id, system_role="admin")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["system_role"] == "admin"

    demoted = _patch(admin_client, signed_in_user.id, system_role="user")
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["system_role"] == "user"

    second, first = events.recent(limit=2)
    assert [(e.event_type, e.metadata) for e in (first, second)] == [
        ("user_role_changed", {"email": signed_in_user.email, "from": "user", "to": "admin"}),
        ("user_role_changed", {"email": signed_in_user.email, "from": "admin", "to": "user"}),
    ]
    assert first.actor_id == admin_user.id
    assert (first.target_type, first.target_id) == ("user", signed_in_user.id)


def test_a_promoted_user_can_manage_users(admin_client, signed_in_client, signed_in_user):
    _patch(admin_client, signed_in_user.id, system_role="admin")

    assert signed_in_client.get("/api/v1/admin/users").status_code == 200


def test_the_last_active_admin_cannot_be_demoted(admin_client, admin_user, create_user):
    deactivated = create_user(email="deactivated-admin@example.com", system_role="admin")
    demoted = create_user(email="demoted-admin@example.com", system_role="admin")
    assert _patch(admin_client, deactivated.id, is_active=False).status_code == 200
    assert _patch(admin_client, demoted.id, system_role="user").status_code == 200

    response = _patch(admin_client, admin_user.id, system_role="user")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"


def test_the_last_active_admin_cannot_be_deactivated(admin_client, admin_user):
    response = _patch(admin_client, admin_user.id, is_active=False)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_admin"
    assert admin_client.get("/api/v1/me").status_code == 200


def test_an_admin_may_step_down_while_another_admin_is_active(
    admin_client, admin_user, create_user
):
    create_user(email="second-admin@example.com", system_role="admin")

    response = _patch(admin_client, admin_user.id, system_role="user")

    assert response.status_code == 200, response.text
    assert admin_client.get("/api/v1/admin/users").status_code == 403


def test_a_rejected_change_changes_nothing(admin_client, admin_user):
    response = _patch(admin_client, admin_user.id, system_role="user", is_active=False)

    assert response.status_code == 409
    [me] = admin_client.get("/api/v1/admin/users").json()["items"]
    assert (me["system_role"], me["is_active"]) == ("admin", True)


RESET_LINK = re.compile(r"/reset-password#token=(?P<token>[A-Za-z0-9_-]+)")


@pytest.fixture
def smtp(app: FastAPI, admin_client, outbox: InMemoryOutbox, clock: FakeClock) -> None:
    """SMTP is set up, so emails reach the outbox."""
    MailService(
        app.state.engine, app.state.settings, sender=outbox, clock=clock
    ).save_smtp_settings(
        host="smtp.example.com",
        port=587,
        security="starttls",
        sender="dawam@example.com",
        username=None,
    )


def _force_reset(client, user_id):
    return client.post(f"/api/v1/admin/users/{user_id}/force-reset")


def _reset_password(client, token: str, password: str):
    return client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "password": password},
        headers={CSRF_HEADER: csrf_token(client)},
    )


@pytest.mark.usefixtures("smtp")
def test_a_forced_reset_emails_a_link_and_ends_the_users_sessions(
    admin_client, signed_in_client, signed_in_user, anonymous_client, outbox: InMemoryOutbox
):
    response = _force_reset(admin_client, signed_in_user.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"delivery": "sent"}
    assert signed_in_client.get("/api/v1/me").status_code == 401
    [message] = outbox.sent_to(signed_in_user.email)
    match = RESET_LINK.search(message.body)
    assert match, message.body
    assert (
        _reset_password(anonymous_client, match["token"], "a fresh new password").status_code == 204
    )
    assert (
        sign_in(anonymous_client, signed_in_user.email, "a fresh new password").status_code == 200
    )


def test_without_smtp_a_forced_reset_link_is_kept_for_the_admin(admin_client, signed_in_user):
    response = _force_reset(admin_client, signed_in_user.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"delivery": "link_for_admin"}
    [link] = admin_client.get("/api/v1/admin/undelivered-links").json()["items"]
    assert (link["recipient"], link["purpose"]) == (signed_in_user.email, "password_reset")


def test_after_a_forced_reset_the_old_password_no_longer_signs_in(
    admin_client, signed_in_user, anonymous_client
):
    _force_reset(admin_client, signed_in_user.id)

    refused = sign_in(anonymous_client, signed_in_user.email, signed_in_user.password)

    assert refused.status_code == 401


def test_a_forced_reset_is_a_security_event(
    admin_client, admin_user, signed_in_client, signed_in_user, events: SecurityEventRecorder
):
    _force_reset(admin_client, signed_in_user.id)

    [event] = [e for e in events.recent() if e.event_type == "password_reset_forced"]
    assert event.actor_id == admin_user.id
    assert (event.target_type, event.target_id) == ("user", signed_in_user.id)
    assert event.metadata == {"email": signed_in_user.email, "sessions_ended": 1}
    assert event.ip == "testclient"


def test_forcing_a_reset_for_an_unknown_user_is_not_found(admin_client):
    response = _force_reset(admin_client, uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def _create(client, **overrides):
    body = {
        "email": "Grace@Example.com",
        "display_name": "Grace Hopper",
        "system_role": "user",
        "temporary_password": "temporary password 1",
    }
    return client.post("/api/v1/admin/users", json={**body, **overrides})


def test_an_admin_creates_a_user_who_must_change_the_temporary_password(
    admin_client, anonymous_client
):
    response = _create(admin_client)

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["email"], created["display_name"], created["system_role"]) == (
        "grace@example.com",
        "Grace Hopper",
        "user",
    )
    assert created["must_change_password"] is True
    assert created["is_active"] is True
    signed_in = sign_in(anonymous_client, "grace@example.com", "temporary password 1")
    assert signed_in.status_code == 200
    assert signed_in.json()["must_change_password"] is True


def test_an_admin_can_create_another_admin(admin_client):
    response = _create(admin_client, system_role="admin")

    assert response.status_code == 201, response.text
    assert response.json()["system_role"] == "admin"


def test_creating_a_user_is_a_security_event(
    admin_client, admin_user, events: SecurityEventRecorder
):
    created = _create(admin_client).json()

    [event] = events.recent(limit=1)
    assert event.event_type == "user_created"
    assert event.actor_id == admin_user.id
    assert (event.target_type, str(event.target_id)) == ("user", created["id"])
    assert event.metadata == {"email": "grace@example.com", "system_role": "user"}
    assert event.ip == "testclient"


def test_a_temporary_password_must_meet_the_password_policy(admin_client):
    response = _create(admin_client, temporary_password="short")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_password"


def test_an_email_that_already_has_a_user_cannot_be_created_again(admin_client, create_user):
    create_user(email="grace@example.com")

    response = _create(admin_client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.usefixtures("smtp")
def test_a_deactivated_user_is_sent_no_reset_link(
    admin_client, signed_in_user, anonymous_client, outbox: InMemoryOutbox
):
    _patch(admin_client, signed_in_user.id, is_active=False)

    response = anonymous_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": signed_in_user.email},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 202
    assert outbox.sent_to(signed_in_user.email) == []


def test_a_reset_link_also_clears_a_temporary_password_flag(
    admin_client, anonymous_client, outbox: InMemoryOutbox, smtp
):
    created = _create(admin_client).json()
    _force_reset(admin_client, created["id"])
    [message] = outbox.sent_to("grace@example.com")
    token = RESET_LINK.search(message.body)["token"]

    assert _reset_password(anonymous_client, token, "chosen by grace").status_code == 204

    signed_in = sign_in(anonymous_client, "grace@example.com", "chosen by grace")
    assert signed_in.json()["must_change_password"] is False


def test_a_deactivated_users_password_cannot_be_force_reset(admin_client, signed_in_user):
    _patch(admin_client, signed_in_user.id, is_active=False)

    response = _force_reset(admin_client, signed_in_user.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_deactivated"
    assert admin_client.get("/api/v1/admin/undelivered-links").json()["items"] == []
