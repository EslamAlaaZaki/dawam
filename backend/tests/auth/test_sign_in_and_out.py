"""Signing in with email and password, ``GET /me``, and signing out (spec stories 2, 4)."""

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
    grace = create_user()

    sign_in(anonymous_client, grace.email, grace.password)

    token = anonymous_client.cookies[SESSION_COOKIE]
    with app.state.engine.connect() as conn:
        sessions = conn.execute(sa.text("SELECT * FROM sessions")).mappings().all()
    assert len(sessions) == 1
    assert sessions[0]["user_id"] == grace.id
    # Only a hash of the token is stored, so a database leak yields no live sessions.
    assert token not in {str(value) for value in sessions[0].values()}


def test_passwords_are_stored_as_argon2id_hashes(app: FastAPI, create_user):
    grace = create_user(password="correct horse battery")

    with app.state.engine.connect() as conn:
        stored = conn.scalar(
            sa.text("SELECT password_hash FROM users WHERE id = :id"), {"id": grace.id}
        )
    assert stored.startswith("$argon2id$")
    assert "correct horse battery" not in stored


def test_sign_out_deletes_the_session_and_the_old_cookie_is_401(
    app: FastAPI, anonymous_client, create_user
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)
    old_token = anonymous_client.cookies[SESSION_COOKIE]

    response = sign_out(anonymous_client)

    assert response.status_code == 204
    [cleared] = set_cookie_headers(response, SESSION_COOKIE)
    assert cookie_attributes(cleared)["max-age"] == "0"
    with app.state.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM sessions")) == 0
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
    app: FastAPI, anonymous_client, create_user
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)
    first_token = anonymous_client.cookies[SESSION_COOKIE]

    sign_in(anonymous_client, grace.email, grace.password)

    assert anonymous_client.cookies[SESSION_COOKIE] != first_token
    with app.state.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM sessions")) == 1
