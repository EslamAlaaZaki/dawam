"""Visitors sign up on their own when an admin allows it (spec stories 1 and 20, and the
registration toggle of §10's auth flows)."""

import uuid
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.admin import SystemSettingsService
from dawam.modules.auth import AuthService, SecurityEventRecorder
from tests.helpers import csrf_token, set_cookie_headers, sign_in

SESSION_COOKIE = "dawam_session"
PASSWORD = "my own long passphrase"

OpenRegistration = Callable[..., None]


@pytest.fixture
def open_registration(app: FastAPI, anonymous_client) -> OpenRegistration:
    """Turn self-registration on (optionally for some domains only), as an admin would."""
    settings = SystemSettingsService(app.state.engine)

    def open_for(*domains: str) -> None:
        settings.set_registration(enabled=True, allowed_email_domains=domains)

    return open_for


def register(client: TestClient, email: str, password: str = PASSWORD, display_name="Grace"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
        headers={"X-CSRF-Token": csrf_token(client)},
    )


def test_registration_is_closed_by_default_and_says_so(anonymous_client):
    assert anonymous_client.get("/api/v1/auth/registration").json() == {"open": False}

    response = register(anonymous_client, "grace@example.com")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_closed"
    assert sign_in(anonymous_client, "grace@example.com", PASSWORD).status_code == 401


def test_a_visitor_signs_up_and_is_signed_in(
    anonymous_client, open_registration, auth_service: AuthService
):
    open_registration()
    assert anonymous_client.get("/api/v1/auth/registration").json() == {"open": True}

    response = register(anonymous_client, " Grace@Example.com ", display_name=" Grace Hopper ")

    assert response.status_code == 201
    me = response.json()
    assert me["email"] == "grace@example.com"
    assert me["display_name"] == "Grace Hopper"
    assert me["system_role"] == "user"
    assert set_cookie_headers(response, SESSION_COOKIE) != []
    assert anonymous_client.get("/api/v1/me").json() == me
    assert len(auth_service.sessions_of(uuid.UUID(me["id"]))) == 1


def test_the_new_account_signs_in_with_its_password(app, anonymous_client, open_registration):
    open_registration()
    register(anonymous_client, "grace@example.com")

    with TestClient(app) as other:
        assert sign_in(other, "grace@example.com", PASSWORD).status_code == 200


def test_only_the_allowed_domains_may_sign_up(anonymous_client, open_registration):
    open_registration("example.com", "acme.org")

    for email in ("grace@other.com", "grace@eng.example.com", "grace@example.com.evil"):
        response = register(anonymous_client, email)
        assert response.status_code == 403, email
        assert response.json()["error"]["code"] == "email_domain_not_allowed"
    assert register(anonymous_client, "grace@ACME.org").status_code == 201


def test_turning_registration_off_closes_it_again(app, anonymous_client, open_registration):
    open_registration()
    SystemSettingsService(app.state.engine).set_registration(
        enabled=False, allowed_email_domains=[]
    )

    assert anonymous_client.get("/api/v1/auth/registration").json() == {"open": False}
    assert register(anonymous_client, "grace@example.com").status_code == 403


@pytest.mark.parametrize(
    ("password", "problem"),
    [("too short", "at least 10 characters"), ("qwertyuiop", "too common")],
)
def test_sign_up_enforces_the_password_policy(
    anonymous_client, open_registration, password, problem
):
    open_registration()

    response = register(anonymous_client, "grace@example.com", password=password)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_password"
    assert problem in response.json()["error"]["message"]
    assert anonymous_client.get("/api/v1/me").status_code == 401


@pytest.mark.parametrize(
    ("email", "display_name", "code"),
    [
        ("not-an-email", "Grace", "invalid_email"),
        ("grace@example.com", "  ", "invalid_display_name"),
    ],
)
def test_sign_up_rejects_an_invalid_email_or_display_name(
    anonymous_client, open_registration, email, display_name, code
):
    open_registration()

    response = register(anonymous_client, email, display_name=display_name)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_an_email_that_has_an_account_cannot_sign_up_again(
    anonymous_client, open_registration, create_user
):
    open_registration()
    existing = create_user(email="grace@example.com")

    response = register(anonymous_client, "GRACE@example.com")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"
    assert sign_in(anonymous_client, existing.email, existing.password).status_code == 200


def test_a_sign_up_is_a_security_event(app, anonymous_client, open_registration, clock):
    open_registration()

    me = register(anonymous_client, "grace@example.com").json()

    recorder = SecurityEventRecorder(app.state.engine, clock=clock)
    [event] = [e for e in recorder.recent() if e.event_type == "user_registered"]
    assert (event.actor_id, event.target_type, event.target_id) == (
        uuid.UUID(me["id"]),
        "user",
        uuid.UUID(me["id"]),
    )
    assert event.metadata == {"email": "grace@example.com"}
    assert event.ip == "testclient"
