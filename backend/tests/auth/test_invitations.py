"""Invitations (spec stories 10, 15; §6.1): an admin invites someone by email so they
can join while self-registration is off. The link is single-use, valid 7 days, stored
hashed and bound to the email; accepting it sets a display name and password, creates
the user and signs them in. Without SMTP the link is kept for the admin to copy."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.auth import Invitations, SecurityEventRecorder
from dawam.modules.mail import MailService
from dawam.platform.clock import FakeClock
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import EmailMessage, InMemoryOutbox
from tests.helpers import csrf_token, sign_in

INVITE = "/api/v1/admin/users/invite"
INVITATIONS = "/api/v1/admin/invitations"
LOOKUP = "/api/v1/auth/invitations/lookup"
ACCEPT = "/api/v1/auth/invitations/accept"
PASSWORD = "a fine new passphrase"
SECOND = timedelta(seconds=1)
LINK = re.compile(r"(?P<url>http\S+/accept-invitation#token=(?P<token>[A-Za-z0-9_-]+))")


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


@pytest.fixture
def events(app: FastAPI, anonymous_client, clock: FakeClock) -> SecurityEventRecorder:
    return SecurityEventRecorder(app.state.engine, clock=clock)


def invite(client: TestClient, email: str = "newcomer@example.com"):
    return client.post(INVITE, json={"email": email})


def post(client: TestClient, path: str, body: dict):
    return client.post(path, json=body, headers={CSRF_HEADER: csrf_token(client)})


def accept(
    client: TestClient, token: str, *, display_name: str = "New Comer", password: str = PASSWORD
):
    return post(
        client, ACCEPT, {"token": token, "display_name": display_name, "password": password}
    )


def token_in(message: EmailMessage) -> str:
    match = LINK.search(message.body)
    assert match, message.body
    return match["token"]


def invited_token(admin_client, outbox: InMemoryOutbox, email: str = "newcomer@example.com") -> str:
    outbox.clear()
    response = invite(admin_client, email)
    assert response.status_code == 201, response.text
    [message] = outbox.sent_to(email)
    return token_in(message)


def pending(admin_client) -> list[dict]:
    response = admin_client.get(INVITATIONS)
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- Inviting -------------------------------------------------------------------------


@pytest.mark.usefixtures("smtp")
def test_an_admin_invites_someone_by_email(admin_client, admin_user, outbox, clock):
    response = invite(admin_client, "  NewComer@Example.com ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["delivery"] == "sent"
    invitation = body["invitation"]
    assert invitation["email"] == "newcomer@example.com"
    assert invitation["invited_by"] == {"id": str(admin_user.id), "display_name": "Root Admin"}
    assert invitation["expires_at"] == (clock() + timedelta(days=7)).isoformat().replace(
        "+00:00", "Z"
    )
    [message] = outbox.sent_to("newcomer@example.com")
    assert message.subject == "You are invited to DAWAM"
    assert "Root Admin" in message.body
    assert "7 days" in message.body
    assert LINK.search(message.body)["url"].startswith(
        "http://localhost:8000/accept-invitation#token="
    )


def test_inviting_an_existing_users_email_is_rejected(admin_client, create_user):
    create_user(email="grace@example.com")

    response = invite(admin_client, "GRACE@example.com")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "email_taken"
    assert "grace@example.com already has an account" in error["message"]
    assert pending(admin_client) == []


def test_an_invalid_email_cannot_be_invited(admin_client):
    response = invite(admin_client, "not an email")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_email"


def test_only_admins_invite(signed_in_client):
    assert invite(signed_in_client).status_code == 403


def test_inviting_is_a_security_event(admin_client, admin_user, events):
    invitation = invite(admin_client).json()["invitation"]

    [event] = events.recent(limit=1)
    assert event.event_type == "invitation_created"
    assert event.actor_id == admin_user.id
    assert (event.target_type, str(event.target_id)) == ("invitation", invitation["id"])
    assert event.metadata == {"email": "newcomer@example.com"}
    assert event.ip == "testclient"


def test_the_token_is_stored_only_hashed(app: FastAPI, admin_client, smtp, outbox):
    """A storage-property check: reads the auth module's own table."""
    token = invited_token(admin_client, outbox)

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM invitations")).mappings().all()
    [row] = rows
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in {str(value) for value in row.values()}


def test_an_invitation_can_carry_a_workspace_and_role(
    app: FastAPI, admin_user, roles, clock, outbox
):
    """The data model supports a Workspace invitation now; T9 adds the endpoint."""
    invitations = Invitations(
        app.state.engine, app.state.settings, mailer=app.state.mailer, clock=clock
    )

    sent = invitations.invite(
        "joiner@example.com",
        actor_id=admin_user.id,
        workspace_id=roles.workspace_id,
        workspace_role="editor",
    )

    assert (sent.invitation.workspace_id, sent.invitation.workspace_role) == (
        roles.workspace_id,
        "editor",
    )
    [listed] = invitations.pending(limit=10).items
    assert (listed.workspace_id, listed.workspace_role) == (roles.workspace_id, "editor")


# --- Without SMTP ---------------------------------------------------------------------


def test_without_smtp_the_link_is_kept_for_the_admin_to_copy(admin_client, anonymous_client):
    response = invite(admin_client)

    assert response.status_code == 201
    assert response.json()["delivery"] == "link_for_admin"
    [link] = admin_client.get("/api/v1/admin/undelivered-links").json()["items"]
    assert (link["recipient"], link["purpose"]) == ("newcomer@example.com", "invitation")
    token = LINK.search(link["url"])["token"]
    assert accept(anonymous_client, token).status_code == 201


def test_accepting_withdraws_the_kept_link(admin_client, anonymous_client):
    invite(admin_client)
    [link] = admin_client.get("/api/v1/admin/undelivered-links").json()["items"]

    accept(anonymous_client, LINK.search(link["url"])["token"])

    assert admin_client.get("/api/v1/admin/undelivered-links").json()["items"] == []


# --- Pending invitations and revoking -------------------------------------------------


@pytest.mark.usefixtures("smtp")
def test_an_admin_sees_pending_invitations_by_email(admin_client, anonymous_client, outbox):
    invite(admin_client, "zoe@example.com")
    token = invited_token(admin_client, outbox, "bob@example.com")
    invite(admin_client, "amy@example.com")
    accept(anonymous_client, token)

    assert [i["email"] for i in pending(admin_client)] == ["amy@example.com", "zoe@example.com"]


def test_expired_invitations_are_not_pending(admin_client, admin_user, clock: FakeClock):
    invite(admin_client)

    clock.advance(timedelta(days=7))

    sign_in(admin_client, admin_user.email, admin_user.password)  # the session timed out
    assert pending(admin_client) == []


def test_pending_invitations_are_cursor_paginated(admin_client):
    for name in ("a", "b", "c"):
        invite(admin_client, f"{name}@example.com")

    first = admin_client.get(INVITATIONS, params={"limit": 2}).json()
    second = admin_client.get(
        INVITATIONS, params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()

    assert [i["email"] for i in first["items"]] == ["a@example.com", "b@example.com"]
    assert [i["email"] for i in second["items"]] == ["c@example.com"]
    assert second["next_cursor"] is None


@pytest.mark.usefixtures("smtp")
def test_inviting_again_replaces_the_pending_invitation(admin_client, anonymous_client, outbox):
    old = invited_token(admin_client, outbox)
    new = invited_token(admin_client, outbox)

    assert len(pending(admin_client)) == 1
    assert accept(anonymous_client, old).json()["error"]["code"] == "invalid_invitation"
    assert accept(anonymous_client, new).status_code == 201


@pytest.mark.usefixtures("smtp")
def test_a_revoked_invitation_no_longer_works(admin_client, anonymous_client, outbox):
    token = invited_token(admin_client, outbox)
    [invitation] = pending(admin_client)

    response = admin_client.delete(f"{INVITATIONS}/{invitation['id']}")

    assert response.status_code == 204
    assert pending(admin_client) == []
    assert accept(anonymous_client, token).json()["error"]["code"] == "invalid_invitation"


def test_revoking_withdraws_the_kept_link(admin_client):
    invitation = invite(admin_client).json()["invitation"]

    admin_client.delete(f"{INVITATIONS}/{invitation['id']}")

    assert admin_client.get("/api/v1/admin/undelivered-links").json()["items"] == []


def test_revoking_is_a_security_event(admin_client, admin_user, events):
    invitation = invite(admin_client).json()["invitation"]

    admin_client.delete(f"{INVITATIONS}/{invitation['id']}")

    [event] = events.recent(limit=1)
    assert event.event_type == "invitation_revoked"
    assert event.actor_id == admin_user.id
    assert (event.target_type, str(event.target_id)) == ("invitation", invitation["id"])
    assert event.metadata == {"email": "newcomer@example.com"}


@pytest.mark.usefixtures("smtp")
def test_an_invitation_that_is_no_longer_pending_cannot_be_revoked(
    admin_client, anonymous_client, outbox
):
    token = invited_token(admin_client, outbox)
    [invitation] = pending(admin_client)
    accept(anonymous_client, token)

    response = admin_client.delete(f"{INVITATIONS}/{invitation['id']}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- Looking up and accepting ---------------------------------------------------------


@pytest.mark.usefixtures("smtp")
def test_the_invitation_page_learns_which_email_it_is_for(
    admin_client, anonymous_client, outbox, clock
):
    token = invited_token(admin_client, outbox)

    response = post(anonymous_client, LOOKUP, {"token": token})

    assert response.status_code == 200
    assert response.json() == {
        "email": "newcomer@example.com",
        "expires_at": (clock() + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    }


def test_looking_up_an_unknown_token_is_rejected(anonymous_client):
    response = post(anonymous_client, LOOKUP, {"token": "no-such-token"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_invitation"


@pytest.mark.usefixtures("smtp")
def test_accepting_creates_the_user_and_signs_them_in(admin_client, anonymous_client, outbox):
    token = invited_token(admin_client, outbox)

    response = accept(anonymous_client, token, display_name="  New Comer ")

    assert response.status_code == 201, response.text
    me = response.json()
    assert (me["email"], me["display_name"], me["system_role"]) == (
        "newcomer@example.com",
        "New Comer",
        "user",
    )
    assert me["must_change_password"] is False
    assert anonymous_client.get("/api/v1/me").json()["email"] == "newcomer@example.com"
    assert sign_in(anonymous_client, "newcomer@example.com", PASSWORD).status_code == 200


@pytest.mark.usefixtures("smtp")
def test_accepting_works_while_self_registration_is_off(admin_client, anonymous_client, outbox):
    assert anonymous_client.get("/api/v1/auth/registration").json() == {"open": False}
    token = invited_token(admin_client, outbox)

    assert accept(anonymous_client, token).status_code == 201


@pytest.mark.usefixtures("smtp")
def test_an_invitation_works_only_once(admin_client, anonymous_client, outbox):
    token = invited_token(admin_client, outbox)
    accept(anonymous_client, token)

    again = accept(anonymous_client, token, password="another passphrase")

    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_invitation"
    assert sign_in(anonymous_client, "newcomer@example.com", PASSWORD).status_code == 200


@pytest.mark.usefixtures("smtp")
def test_an_invitation_works_for_7_days(admin_client, anonymous_client, outbox, clock: FakeClock):
    token = invited_token(admin_client, outbox)

    clock.advance(timedelta(days=7) - SECOND)

    assert accept(anonymous_client, token).status_code == 201


@pytest.mark.usefixtures("smtp")
def test_an_expired_invitation_is_rejected(
    admin_client, anonymous_client, outbox, clock: FakeClock
):
    token = invited_token(admin_client, outbox)

    clock.advance(timedelta(days=7))

    response = accept(anonymous_client, token)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_invitation"
    assert post(anonymous_client, LOOKUP, {"token": token}).status_code == 400


def test_an_unknown_token_is_rejected(anonymous_client):
    response = accept(anonymous_client, "made-up-token")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_invitation"


@pytest.mark.usefixtures("smtp")
def test_an_invitation_is_bound_to_its_email(admin_client, anonymous_client, outbox, create_user):
    """Someone else took the email (e.g. an admin created the account) before it was
    accepted: the invitation cannot make a second account for it."""
    token = invited_token(admin_client, outbox, "grace@example.com")
    create_user(email="grace@example.com")

    response = accept(anonymous_client, token)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


@pytest.mark.usefixtures("smtp")
@pytest.mark.parametrize(
    ("password", "code"),
    [("short", "invalid_password"), ("password123", "invalid_password")],
)
def test_the_password_policy_applies(admin_client, anonymous_client, outbox, password, code):
    token = invited_token(admin_client, outbox)

    response = accept(anonymous_client, token, password=password)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    # A rejected attempt leaves the invitation usable.
    assert accept(anonymous_client, token).status_code == 201


@pytest.mark.usefixtures("smtp")
def test_a_display_name_is_required(admin_client, anonymous_client, outbox):
    token = invited_token(admin_client, outbox)

    response = accept(anonymous_client, token, display_name="   ")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_display_name"


@pytest.mark.usefixtures("smtp")
def test_accepting_is_a_security_event(admin_client, admin_user, anonymous_client, outbox, events):
    token = invited_token(admin_client, outbox)
    [invitation] = pending(admin_client)

    me = accept(anonymous_client, token).json()

    [event] = events.recent(limit=1)
    assert event.event_type == "invitation_accepted"
    assert str(event.actor_id) == me["id"]
    assert (event.target_type, str(event.target_id)) == ("user", me["id"])
    assert event.metadata == {
        "email": "newcomer@example.com",
        "invitation_id": invitation["id"],
        "invited_by": str(admin_user.id),
    }
