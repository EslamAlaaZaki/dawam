"""A user an admin created with a temporary password must change it first (spec story
15a, §6.1): until then their session allows only ``GET /me``, the password change and
signing out; everything else gets ``403 password_change_required``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token, sign_in

TEMPORARY = "temporary password 1"


@pytest.fixture
def flagged_client(app: FastAPI, admin_client) -> Iterator[TestClient]:
    """Signed in as a user the admin just created with a temporary password."""
    created = admin_client.post(
        "/api/v1/admin/users",
        json={
            "email": "grace@example.com",
            "display_name": "Grace Hopper",
            "system_role": "user",
            "temporary_password": TEMPORARY,
        },
    )
    assert created.status_code == 201, created.text
    with TestClient(app) as client:
        assert sign_in(client, "grace@example.com", TEMPORARY).status_code == 200
        client.headers[CSRF_HEADER] = csrf_token(client)
        yield client


def _change_password(client: TestClient, new_password: str = "my very own password"):
    return client.post(
        "/api/v1/auth/password/change",
        json={"current_password": TEMPORARY, "new_password": new_password},
    )


def test_a_flagged_user_may_read_who_they_are(flagged_client):
    response = flagged_client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/workspaces"),
        ("POST", "/api/v1/workspaces"),
        ("PATCH", "/api/v1/me"),
    ],
)
def test_a_flagged_user_may_do_nothing_else(flagged_client, method, path):
    response = flagged_client.request(method, path, json={"name": "X", "display_name": "X"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_change_required"


def test_a_flagged_admin_may_not_use_the_admin_console(app: FastAPI, admin_client):
    admin_client.post(
        "/api/v1/admin/users",
        json={
            "email": "deputy@example.com",
            "display_name": "Deputy",
            "system_role": "admin",
            "temporary_password": TEMPORARY,
        },
    )
    with TestClient(app) as deputy:
        sign_in(deputy, "deputy@example.com", TEMPORARY)

        response = deputy.get("/api/v1/admin/users")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "password_change_required"


def test_a_flagged_user_may_sign_out(flagged_client):
    assert flagged_client.post("/api/v1/auth/logout").status_code == 204
    assert flagged_client.get("/api/v1/me").status_code == 401


def test_a_flagged_user_may_sign_out_everywhere(flagged_client):
    assert flagged_client.post("/api/v1/auth/logout-all").status_code == 204


def test_changing_the_password_clears_the_flag_and_opens_everything(flagged_client):
    assert _change_password(flagged_client).status_code == 204

    assert flagged_client.get("/api/v1/me").json()["must_change_password"] is False
    assert flagged_client.get("/api/v1/workspaces").status_code == 200


def test_the_new_password_must_differ_from_the_temporary_one(flagged_client):
    response = _change_password(flagged_client, new_password=TEMPORARY)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "password_unchanged"
    assert flagged_client.get("/api/v1/workspaces").status_code == 403
