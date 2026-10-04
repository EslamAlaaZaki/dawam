"""Every state-changing request needs a valid double-submit CSRF token (spec §6.1):
the ``X-CSRF-Token`` header must equal the ``dawam_csrf`` cookie."""

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.platform.csrf import CSRF_COOKIE, CSRF_HEADER
from tests.helpers import cookie_attributes, csrf_token, set_cookie_headers, sign_in

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def error_code(response) -> str:
    return response.json()["error"]["code"]


def login(client: TestClient, headers: dict[str, str], email: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=headers
    )


def test_any_response_gives_a_client_without_one_a_csrf_cookie(anonymous_client):
    response = anonymous_client.get("/api/v1/version")

    [cookie] = set_cookie_headers(response, CSRF_COOKIE)
    attributes = cookie_attributes(cookie)
    assert "httponly" not in attributes  # the frontend must read it
    assert attributes["samesite"].lower() == "lax"
    assert attributes["path"] == "/"
    assert len(anonymous_client.cookies[CSRF_COOKIE]) >= 32


def test_the_csrf_cookie_is_kept_once_set(anonymous_client):
    token = csrf_token(anonymous_client)

    response = anonymous_client.get("/api/v1/version")

    assert set_cookie_headers(response, CSRF_COOKIE) == []
    assert anonymous_client.cookies[CSRF_COOKIE] == token


def test_the_csrf_cookie_is_secure_over_tls(app: FastAPI):
    with TestClient(app, base_url="https://dawam.test") as client:
        response = client.get("/api/v1/version")

    [cookie] = set_cookie_headers(response, CSRF_COOKIE)
    assert "secure" in cookie_attributes(cookie)


def test_sign_in_without_a_csrf_token_is_rejected(anonymous_client, create_user):
    grace = create_user()
    csrf_token(anonymous_client)

    response = login(anonymous_client, {}, grace.email, grace.password)

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"
    assert anonymous_client.get("/api/v1/me").status_code == 401


def test_a_token_that_does_not_match_the_cookie_is_rejected(anonymous_client, create_user):
    grace = create_user()
    csrf_token(anonymous_client)

    response = login(anonymous_client, {CSRF_HEADER: "forged" * 8}, grace.email, grace.password)

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_a_header_without_the_cookie_is_rejected(app: FastAPI, create_user):
    grace = create_user()
    with TestClient(app) as client:
        response = login(client, {CSRF_HEADER: "a" * 43}, grace.email, grace.password)

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"


def test_sign_out_without_a_csrf_token_is_rejected_and_keeps_the_session(
    anonymous_client, create_user
):
    grace = create_user()
    sign_in(anonymous_client, grace.email, grace.password)

    response = anonymous_client.post("/api/v1/auth/logout")

    assert response.status_code == 403
    assert error_code(response) == "csrf_failed"
    assert anonymous_client.get("/api/v1/me").status_code == 200


def test_safe_requests_need_no_csrf_token(signed_in_client):
    del signed_in_client.headers[CSRF_HEADER]

    assert signed_in_client.get("/api/v1/me").status_code == 200


def _unsafe_api_operations(app: FastAPI) -> list[tuple[str, str]]:
    operations = []
    for path, methods in app.openapi()["paths"].items():
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        operations += [(m.upper(), concrete) for m in methods if m.upper() in UNSAFE_METHODS]
    return operations


def test_every_state_changing_api_route_requires_the_token(app: FastAPI, signed_in_client):
    operations = _unsafe_api_operations(app)
    assert ("POST", "/api/v1/auth/login") in operations
    del signed_in_client.headers[CSRF_HEADER]

    for method, path in operations:
        response = signed_in_client.request(method, path, json={})
        assert response.status_code == 403, f"{method} {path}"
        assert error_code(response) == "csrf_failed", f"{method} {path}"
