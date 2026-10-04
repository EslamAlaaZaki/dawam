"""Signing in with email and password, ``GET /me``, and signing out (spec stories 2, 4)."""

from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.auth import AuthService, SessionInfo
from dawam.platform.clock import FakeClock
from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import cookie_attributes, csrf_token, set_cookie_headers, sign_in, sign_out

SESSION_COOKIE = "dawam_session"


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    return body["error"]


def test_sign_in_returns_the_user_and_me_returns_them_too(anonymous_client, create_user):
    grace = create_user(email="grace@example.com", display_name="Grace Hopper")

    response = sign_in(anonymous_client, "grace@example.com", grace.password)

    assert response.status_code == 200
    expected = {
        "id": str(grace.id),
        "email": "grace@example.com",
        "display_name": "Grace Hopper",
        "system_role": "user",
    }
    assert response.json() == expected
    me = anonymous_client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json() == expected


def test_email_is_matched_case_insensitively(anonymous_client, create_user):
    grace = create_user(email="grace@example.com")

    response = sign_in(anonymous_client, "  Grace@Example.COM ", grace.password)

    assert response.status_code == 200
    assert response.json()["email"] == "grace@example.com"


def test_anonymous_me_is_401_in_the_standard_error_shape(anonymous_client):
    response = anonymous_client.get("/api/v1/me")

    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"


def test_an_unknown_session_cookie_is_401(anonymous_client):
    anonymous_client.cookies.set(SESSION_COOKIE, "not-a-real-session")

    response = anonymous_client.get("/api/v1/me")

    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"


def test_wrong_email_and_wrong_password_get_the_same_error(anonymous_client, create_user):
    grace = create_user(email="grace@example.com")

    wrong_password = sign_in(anonymous_client, "grace@example.com", "not the password")
    wrong_email = sign_in(anonymous_client, "nobody@example.com", grace.password)

    assert wrong_password.status_code == wrong_email.status_code == 401
    assert wrong_password.json() == wrong_email.json()
    assert error_of(wrong_password)["code"] == "invalid_credentials"
    assert set_cookie_headers(wrong_password, SESSION_COOKIE) == []
    assert anonymous_client.get("/api/v1/me").status_code == 401


def test_sign_in_rejects_a_missing_field(anonymous_client):
    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": "grace@example.com"},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


@pytest.mark.parametrize(
    "credentials",
    [
        {"email": "a" * 243 + "@example.com", "password": "correct horse battery"},  # 255
        {"email": "grace@example.com", "password": "x" * 1025},
    ],
)
def test_sign_in_rejects_huge_inputs_before_checking_them(anonymous_client, credentials):
    response = anonymous_client.post(
        "/api/v1/auth/login", json=credentials, headers={CSRF_HEADER: csrf_token(anonymous_client)}
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


def test_sign_in_records_when_from_where_and_with_what(
    anonymous_client, create_user, auth_service: AuthService, clock: FakeClock
):
    grace = create_user()
    assert auth_service.get_user(grace.id).last_login_at is None
    signed_in_at = clock()

    response = anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": grace.email, "password": grace.password},
        headers={
            CSRF_HEADER: csrf_token(anonymous_client),
            "User-Agent": "Mozilla/5.0 (DAWAM test)",
            # Only uvicorn, and only for DAWAM_FORWARDED_ALLOW_IPS, may rewrite the client.
            "X-Forwarded-For": "203.0.113.9",
        },
    )

    assert response.status_code == 200
    assert auth_service.get_user(grace.id).last_login_at == signed_in_at
    assert auth_service.sessions_of(grace.id) == [
        SessionInfo(
            ip="testclient",
            user_agent="Mozilla/5.0 (DAWAM test)",
            created_at=signed_in_at,
            last_seen_at=signed_in_at,
        )
    ]


def test_only_a_successful_sign_in_updates_the_last_login(
    anonymous_client, create_user, auth_service: AuthService, clock: FakeClock
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)
    first = clock()

    clock.advance(timedelta(hours=1))
    sign_in(anonymous_client, grace.email, "not the password")
    assert auth_service.get_user(grace.id).last_login_at == first

    clock.advance(timedelta(hours=1))
    sign_in(anonymous_client, grace.email, grace.password)
    assert auth_service.get_user(grace.id).last_login_at == first + timedelta(hours=2)


def test_a_very_long_user_agent_is_cut_short(
    anonymous_client, create_user, auth_service: AuthService
):
    grace = create_user()

    anonymous_client.post(
        "/api/v1/auth/login",
        json={"email": grace.email, "password": grace.password},
        headers={CSRF_HEADER: csrf_token(anonymous_client), "User-Agent": "x" * 5000},
    )

    [session] = auth_service.sessions_of(grace.id)
    assert session.user_agent == "x" * 512


def test_the_session_cookie_is_httponly_lax_and_not_secure_over_plain_http(
    anonymous_client, create_user
):
    grace = create_user()

    response = sign_in(anonymous_client, grace.email, grace.password)

    [cookie] = set_cookie_headers(response, SESSION_COOKIE)
    attributes = cookie_attributes(cookie)
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    assert attributes["path"] == "/"
    assert "secure" not in attributes
    assert int(attributes["max-age"]) == 14 * 24 * 3600


def test_the_session_cookie_is_secure_when_served_over_tls(app: FastAPI, create_user):
    grace = create_user()

    with TestClient(app, base_url="https://dawam.test") as client:
        response = sign_in(client, grace.email, grace.password)
        me = client.get("/api/v1/me")

    [cookie] = set_cookie_headers(response, SESSION_COOKIE)
    attributes = cookie_attributes(cookie)
    assert "secure" in attributes
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    assert me.status_code == 200


def test_the_session_is_stored_in_the_database_not_in_the_cookie(
    app: FastAPI, anonymous_client, create_user
):
    """A deliberate storage-property check: it reads the auth tables directly."""
    grace = create_user()

    sign_in(anonymous_client, grace.email, grace.password)

    token = anonymous_client.cookies[SESSION_COOKIE]
    with app.state.engine.connect() as conn:
        sessions = conn.execute(sa.text("SELECT * FROM sessions")).mappings().all()
    assert len(sessions) == 1
    assert sessions[0]["user_id"] == grace.id
    # Only a hash of the token is stored, so a database leak yields no live sessions.
    assert token not in {str(value) for value in sessions[0].values()}


def test_the_session_row_holds_its_absolute_expiry(
    app: FastAPI, anonymous_client, create_user, clock: FakeClock
):
    """A deliberate storage-property check: it reads the auth tables directly."""
    grace = create_user()

    sign_in(anonymous_client, grace.email, grace.password)

    with app.state.engine.connect() as conn:
        created_at, expires_at = conn.execute(
            sa.text("SELECT created_at, expires_at FROM sessions")
        ).one()
    assert created_at == clock()
    assert expires_at == clock() + timedelta(days=14)


def test_passwords_are_stored_as_argon2id_hashes(app: FastAPI, create_user):
    """A deliberate storage-property check: it reads the auth tables directly."""
    grace = create_user(password="correct horse battery")

    with app.state.engine.connect() as conn:
        stored = conn.scalar(
            sa.text("SELECT password_hash FROM users WHERE id = :id"), {"id": grace.id}
        )
    assert stored.startswith("$argon2id$")
    assert "correct horse battery" not in stored


def test_sign_out_deletes_the_session_and_the_old_cookie_is_401(
    anonymous_client, create_user, auth_service: AuthService
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)
    old_token = anonymous_client.cookies[SESSION_COOKIE]

    response = sign_out(anonymous_client)

    assert response.status_code == 204
    [cleared] = set_cookie_headers(response, SESSION_COOKIE)
    assert cookie_attributes(cleared)["max-age"] == "0"
    assert auth_service.sessions_of(grace.id) == []
    anonymous_client.cookies.set(SESSION_COOKIE, old_token)
    me = anonymous_client.get("/api/v1/me")
    assert me.status_code == 401
    assert error_of(me)["code"] == "unauthenticated"


def test_sign_out_ends_only_that_session(app: FastAPI, create_user):
    grace = create_user()
    with TestClient(app) as laptop, TestClient(app) as phone:
        sign_in(laptop, grace.email, grace.password)
        sign_in(phone, grace.email, grace.password)

        sign_out(laptop)

        assert laptop.get("/api/v1/me").status_code == 401
        assert phone.get("/api/v1/me").status_code == 200


def test_sign_out_without_a_session_is_harmless(anonymous_client):
    assert sign_out(anonymous_client).status_code == 204


def test_signing_in_again_replaces_the_previous_session(
    anonymous_client, create_user, auth_service: AuthService
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)
    first_token = anonymous_client.cookies[SESSION_COOKIE]

    sign_in(anonymous_client, grace.email, grace.password)

    assert anonymous_client.cookies[SESSION_COOKIE] != first_token
    assert len(auth_service.sessions_of(grace.id)) == 1
    anonymous_client.cookies.set(SESSION_COOKIE, first_token)
    assert anonymous_client.get("/api/v1/me").status_code == 401
