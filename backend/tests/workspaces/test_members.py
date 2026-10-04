"""Workspace membership (spec stories 30-34, §6.2): owners add people by email with a
role, invite people who have no account yet, change roles, remove members and transfer
ownership; any member can leave. A Workspace never ends up without an owner."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.admin import SystemSettingsService
from dawam.modules.mail import MailService
from dawam.platform.clock import FakeClock
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import InMemoryOutbox
from tests.helpers import csrf_token
from tests.roles import RoleClients

LINK = re.compile(r"/accept-invitation#token=(?P<token>[A-Za-z0-9_-]+)")


def members_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/members"


def member_path(roles: RoleClients, user_id: object) -> str:
    return f"{members_path(roles)}/{user_id}"


def listed(roles: RoleClients, as_role="owner") -> dict[str, str]:
    """email -> role of every member, as ``as_role`` sees the member list."""
    response = roles.client(as_role).get(members_path(roles))
    assert response.status_code == 200, response.text
    return {m["email"]: m["role"] for m in response.json()["items"]}


def add(roles: RoleClients, email: str, role: str = "editor", as_role="owner"):
    return roles.client(as_role).post(members_path(roles), json={"email": email, "role": role})


def error_code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
def colleague(roles: RoleClients, auth_service):
    """A user with an account who is not yet a member of the Workspace."""
    return auth_service.create_user(
        email="colleague@example.com",
        password="colleague password",
        display_name="Colleague",
        system_role="user",
    )


@pytest.fixture
def smtp(app: FastAPI, outbox: InMemoryOutbox, clock: FakeClock) -> None:
    MailService(
        app.state.engine, app.state.settings, sender=outbox, clock=clock
    ).save_smtp_settings(
        host="smtp.example.com",
        port=587,
        security="starttls",
        sender="d@example.com",
        username=None,
    )


@pytest.fixture
def open_registration(app: FastAPI):
    def open_for(*domains: str) -> None:
        SystemSettingsService(app.state.engine).set_registration(
            enabled=True, allowed_email_domains=domains
        )

    return open_for


# Listing


def test_every_member_sees_the_members_with_their_roles(roles: RoleClients):
    roles.client("editor")
    roles.client("viewer")

    response = roles.client("viewer").get(members_path(roles))

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(m["display_name"], m["email"], m["role"]) for m in items] == [
        ("Editor", "editor@example.com", "editor"),
        ("Owner", "owner@example.com", "owner"),
        ("Viewer", "viewer@example.com", "viewer"),
    ]
    assert {m["user_id"] for m in items} == {
        str(roles.user(r).id) for r in ("editor", "owner", "viewer")
    }


# Adding an existing user


def test_an_owner_adds_an_existing_user_by_email_with_a_role(roles: RoleClients, colleague):
    response = add(roles, "  Colleague@Example.com ", "viewer")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "added"
    assert body["member"]["user_id"] == str(colleague.id)
    assert body["member"]["role"] == "viewer"
    assert listed(roles)["colleague@example.com"] == "viewer"


@pytest.mark.parametrize("role", ["owner", "editor", "viewer"])
def test_each_role_can_be_given(roles: RoleClients, colleague, role):
    assert add(roles, colleague.email, role).status_code == 201
    assert listed(roles)[colleague.email] == role


def test_the_added_user_can_open_the_workspace(roles: RoleClients, colleague, app):
    add(roles, colleague.email, "viewer")

    with TestClient(app) as client:
        client.headers[CSRF_HEADER] = csrf_token(client)
        client.post(
            "/api/v1/auth/login", json={"email": colleague.email, "password": "colleague password"}
        )
        assert client.get(f"/api/v1/workspaces/{roles.workspace_id}").json()["role"] == "viewer"


def test_adding_a_member_twice_is_refused(roles: RoleClients, colleague):
    add(roles, colleague.email)

    response = add(roles, colleague.email, "viewer")

    assert response.status_code == 409
    assert error_code(response) == "already_member"
    assert listed(roles)[colleague.email] == "editor"


def test_an_unknown_role_is_refused(roles: RoleClients, colleague):
    response = add(roles, colleague.email, "admin")

    assert response.status_code == 422


def test_editors_and_viewers_cannot_add_members(roles: RoleClients, colleague):
    for role in ("editor", "viewer"):
        response = add(roles, colleague.email, as_role=role)
        assert response.status_code == 403, role

    assert colleague.email not in listed(roles)


# Inviting someone without an account


def test_an_owner_may_not_invite_while_registration_is_closed(roles: RoleClients):
    response = add(roles, "newcomer@example.com")

    assert response.status_code == 403
    assert error_code(response) == "invite_not_allowed"


def test_an_owner_invites_a_non_user_when_registration_is_open(
    roles: RoleClients, open_registration, smtp, outbox: InMemoryOutbox, anonymous_client
):
    open_registration()

    response = add(roles, "Newcomer@Example.com", "viewer")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "invited"
    assert body["invitation"]["email"] == "newcomer@example.com"
    assert body["delivery"] == "sent"
    [message] = outbox.sent_to("newcomer@example.com")
    assert "Permission Matrix" in message.body
    token = LINK.search(message.body)["token"]  # type: ignore[index]

    accepted = anonymous_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "display_name": "New Comer", "password": "a fine new passphrase"},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert accepted.status_code == 201, accepted.text
    assert listed(roles)["newcomer@example.com"] == "viewer"
    opened = anonymous_client.get(f"/api/v1/workspaces/{roles.workspace_id}")
    assert opened.json()["role"] == "viewer"


def test_invites_follow_the_registration_domains(roles: RoleClients, open_registration):
    open_registration("bank.example")

    refused = add(roles, "someone@elsewhere.example")
    allowed = add(roles, "someone@bank.example")

    assert refused.status_code == 403
    assert error_code(refused) == "invite_not_allowed"
    assert allowed.status_code == 201, allowed.text


def test_an_admin_owner_may_invite_while_registration_is_closed(roles: RoleClients):
    admin_workspace = roles.client("admin").post("/api/v1/workspaces", json={"name": "Admin's"})
    workspace_id = admin_workspace.json()["id"]

    response = roles.client("admin").post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": "newcomer@example.com", "role": "editor"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "invited"
    # Without SMTP the link waits for an admin to share.
    assert response.json()["delivery"] == "link_for_admin"


# Changing roles


def test_an_owner_changes_a_members_role(roles: RoleClients):
    roles.client("viewer")

    response = roles.client("owner").patch(
        member_path(roles, roles.user("viewer").id), json={"role": "editor"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "editor"
    assert listed(roles)["viewer@example.com"] == "editor"


def test_changing_the_role_of_a_non_member_is_not_found(roles: RoleClients, colleague):
    response = roles.client("owner").patch(
        member_path(roles, colleague.id), json={"role": "editor"}
    )

    assert response.status_code == 404
    assert error_code(response) == "member_not_found"


def test_the_last_owner_cannot_be_demoted(roles: RoleClients):
    response = roles.client("owner").patch(
        member_path(roles, roles.user("owner").id), json={"role": "editor"}
    )

    assert response.status_code == 409
    assert error_code(response) == "last_owner"
    assert listed(roles)["owner@example.com"] == "owner"


def test_an_owner_may_step_down_once_another_owner_exists(roles: RoleClients):
    roles.client("editor")
    roles.client("owner").patch(member_path(roles, roles.user("editor").id), json={"role": "owner"})

    response = roles.client("owner").patch(
        member_path(roles, roles.user("owner").id), json={"role": "viewer"}
    )

    assert response.status_code == 200, response.text
    assert listed(roles, "editor") == {
        "editor@example.com": "owner",
        "owner@example.com": "viewer",
    }


def test_editors_cannot_change_roles(roles: RoleClients):
    roles.client("viewer")

    response = roles.client("editor").patch(
        member_path(roles, roles.user("viewer").id), json={"role": "editor"}
    )

    assert response.status_code == 403


# Removing and leaving


def test_a_removed_member_loses_access_at_once(roles: RoleClients):
    viewer = roles.client("viewer")
    assert viewer.get(f"/api/v1/workspaces/{roles.workspace_id}").status_code == 200

    response = roles.client("owner").delete(member_path(roles, roles.user("viewer").id))

    assert response.status_code == 204
    assert viewer.get(f"/api/v1/workspaces/{roles.workspace_id}").status_code == 404
    assert "viewer@example.com" not in listed(roles)


def test_the_last_owner_cannot_be_removed(roles: RoleClients):
    response = roles.client("owner").delete(member_path(roles, roles.user("owner").id))

    assert response.status_code == 409
    assert error_code(response) == "last_owner"


def test_removing_a_non_member_is_not_found(roles: RoleClients, colleague):
    response = roles.client("owner").delete(member_path(roles, colleague.id))

    assert response.status_code == 404
    assert error_code(response) == "member_not_found"


def test_editors_cannot_remove_members(roles: RoleClients):
    roles.client("viewer")

    response = roles.client("editor").delete(member_path(roles, roles.user("viewer").id))

    assert response.status_code == 403
    assert "viewer@example.com" in listed(roles)


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_any_member_can_leave(roles: RoleClients, role):
    client = roles.client(role)

    response = client.post(f"/api/v1/workspaces/{roles.workspace_id}/leave")

    assert response.status_code == 204
    assert client.get(f"/api/v1/workspaces/{roles.workspace_id}").status_code == 404
    assert client.get("/api/v1/workspaces").json()["items"] == []


def test_the_last_owner_cannot_leave(roles: RoleClients):
    response = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/leave")

    assert response.status_code == 409
    assert error_code(response) == "last_owner"


def test_an_owner_may_leave_once_another_owner_exists(roles: RoleClients):
    roles.client("editor")
    roles.client("owner").patch(member_path(roles, roles.user("editor").id), json={"role": "owner"})

    response = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/leave")

    assert response.status_code == 204
    assert listed(roles, "editor") == {"editor@example.com": "owner"}


# Transferring ownership


def test_an_owner_transfers_ownership_and_becomes_an_editor(roles: RoleClients):
    roles.client("viewer")

    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/transfer-ownership",
        json={"user_id": str(roles.user("viewer").id)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "editor"
    assert "workspace.manage_members" not in response.json()["permissions"]
    assert listed(roles, "viewer") == {
        "owner@example.com": "editor",
        "viewer@example.com": "owner",
    }


def test_ownership_goes_only_to_a_member(roles: RoleClients, colleague):
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/transfer-ownership",
        json={"user_id": str(colleague.id)},
    )

    assert response.status_code == 404
    assert error_code(response) == "member_not_found"


def test_ownership_cannot_be_transferred_to_yourself(roles: RoleClients):
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/transfer-ownership",
        json={"user_id": str(roles.user("owner").id)},
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_transfer"
    assert listed(roles)["owner@example.com"] == "owner"


def test_only_owners_transfer_ownership(roles: RoleClients):
    roles.client("viewer")

    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/transfer-ownership",
        json={"user_id": str(roles.user("viewer").id)},
    )

    assert response.status_code == 403
