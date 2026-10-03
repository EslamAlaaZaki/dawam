"""Helpers for tests that sign in through the API, as the frontend does."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from dawam.platform.csrf import CSRF_COOKIE, CSRF_HEADER


def csrf_token(client: TestClient) -> str:
    """The client's CSRF cookie value; any response sets it if the client has none yet."""
    if CSRF_COOKIE not in client.cookies:
        client.get("/api/v1/version")
    return client.cookies[CSRF_COOKIE]


def sign_in(client: TestClient, email: str, password: str) -> Any:
    """``POST /api/v1/auth/login`` with the double-submit CSRF token."""
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={CSRF_HEADER: csrf_token(client)},
    )


def sign_out(client: TestClient) -> Any:
    return client.post("/api/v1/auth/logout", headers={CSRF_HEADER: csrf_token(client)})


def set_cookie_headers(response: Any, name: str) -> list[str]:
    """Every ``Set-Cookie`` header of ``response`` that sets the cookie ``name``."""
    return [
        value
        for value in response.headers.get_list("set-cookie")
        if value.split("=", 1)[0].strip() == name
    ]


def cookie_attributes(set_cookie: str) -> dict[str, str]:
    """``"a=b; HttpOnly; SameSite=lax"`` -> ``{"httponly": "", "samesite": "lax"}``."""
    attributes: dict[str, str] = {}
    for part in set_cookie.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        attributes[key.lower()] = value
    return attributes
