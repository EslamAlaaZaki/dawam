"""A signed-in user edits their display name, changes their password and signs out
everywhere (spec stories 5, 7, 8; §6.1 and the §10 auth flows)."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.auth import AuthService
from dawam.platform.csrf import CSRF_HEADER
from tests.conftest import CreatedUser
from tests.helpers import csrf_token, set_cookie_headers, sign_in

SESSION_COOKIE = "dawam_session"
NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def other_browser(app: FastAPI, signed_in_user: CreatedUser) -> Iterator[TestClient]:
    """A second browser signed in as the same user."""
    with TestClient(app) as client:
        assert sign_in(client, signed_in_user.email, signed_in_user.password).status_code == 200
        yield client


def is_signed_in(client: TestClient) -> bool:
    return client.get("/api/v1/me").status_code == 200


def change_password(client: TestClient, current: str, new: str):
    return client.post(
        "/api/v1/auth/password/change",
        json={"current_password": current, "new_password": new},
    )


# --- Display name -----------------------------------------------------------------


def test_a_user_edits_their_display_name(signed_in_client, signed_in_user):
    response = signed_in_client.patch("/api/v1/me", json={"display_name": "  Countess Ada "})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Countess Ada"
    assert response.json()["email"] == signed_in_user.email
    assert signed_in_client.get("/api/v1/me").json()["display_name"] == "Countess Ada"


@pytest.mark.parametrize("display_name", ["", "   ", "x" * 201])
def test_a_blank_or_overlong_display_name_is_rejected(signed_in_client, display_name):
    response = signed_in_client.patch("/api/v1/me", json={"display_name": display_name})

    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"invalid_display_name", "validation_error"}
    assert signed_in_client.get("/api/v1/me").json()["display_name"] == "Ada Lovelace"


def test_anonymous_visitors_cannot_edit_a_display_name(anonymous_client):
    response = anonymous_client.patch(
        "/api/v1/me",
        json={"display_name": "Nobody"},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 401


# --- Changing the password ----------------------------------------------------------


def test_changing_the_password_needs_the_current_one_and_replaces_it(
    signed_in_client, signed_in_user, anonymous_client
):
    response = change_password(signed_in_client, signed_in_user.password, NEW_PASSWORD)

    assert response.status_code == 204
    old = sign_in(anonymous_client, signed_in_user.email, signed_in_user.password)
    assert old.status_code == 401
    assert sign_in(anonymous_client, signed_in_user.email, NEW_PASSWORD).status_code == 200


def test_changing_the_password_ends_every_other_session_but_keeps_this_one(
    signed_in_client, signed_in_user, other_browser, auth_service: AuthService
):
    assert len(auth_service.sessions_of(signed_in_user.id)) == 2

    change_password(signed_in_client, signed_in_user.password, NEW_PASSWORD)

    assert is_signed_in(signed_in_client)
    assert not is_signed_in(other_browser)
    assert len(auth_service.sessions_of(signed_in_user.id)) == 1


def test_a_wrong_current_password_changes_nothing(
    signed_in_client, signed_in_user, other_browser, anonymous_client
):
    response = change_password(signed_in_client, "not my password", NEW_PASSWORD)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "wrong_password"
    assert is_signed_in(signed_in_client)
    assert is_signed_in(other_browser)
    assert sign_in(anonymous_client, signed_in_user.email, NEW_PASSWORD).status_code == 401


@pytest.mark.parametrize(
    ("new_password", "problem"),
    [("too short", "at least 10 characters"), ("Password123", "too common")],
)
def test_the_new_password_must_meet_the_policy(
    signed_in_client, signed_in_user, other_browser, new_password, problem
):
    response = change_password(signed_in_client, signed_in_user.password, new_password)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_password"
    assert problem in response.json()["error"]["message"]
    assert is_signed_in(other_browser)


def test_anonymous_visitors_cannot_change_a_password(anonymous_client, create_user):
    grace = create_user()

    response = anonymous_client.post(
        "/api/v1/auth/password/change",
        json={"current_password": grace.password, "new_password": NEW_PASSWORD},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 401


def test_an_oversized_password_is_rejected_before_it_is_checked(signed_in_client, signed_in_user):
    response = change_password(signed_in_client, "x" * 1025, NEW_PASSWORD)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- Signing out everywhere -----------------------------------------------------------


def test_signing_out_everywhere_ends_every_session_of_the_user(
    signed_in_client, signed_in_user, other_browser, create_user, app, auth_service
):
    grace = create_user(email="grace@example.com")
    with TestClient(app) as graces_browser:
        sign_in(graces_browser, grace.email, grace.password)

        response = signed_in_client.post("/api/v1/auth/logout-all")

        assert response.status_code == 204
        assert set_cookie_headers(response, SESSION_COOKIE) != []  # the cookie is cleared
        assert not is_signed_in(signed_in_client)
        assert not is_signed_in(other_browser)
        assert auth_service.sessions_of(signed_in_user.id) == []
        assert is_signed_in(graces_browser)  # other users are not affected


def test_anonymous_visitors_cannot_sign_out_everywhere(anonymous_client):
    response = anonymous_client.post(
        "/api/v1/auth/logout-all", headers={CSRF_HEADER: csrf_token(anonymous_client)}
    )

    assert response.status_code == 401
