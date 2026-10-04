"""Fixtures for the admin module's tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.platform.csrf import CSRF_HEADER
from tests.conftest import CreatedUser, UserFactory
from tests.helpers import csrf_token, sign_in


@pytest.fixture
def admin_user(create_user: UserFactory) -> CreatedUser:
    return create_user(
        email="root@example.com",
        password="installation keeper",
        display_name="Root Admin",
        system_role="admin",
    )


@pytest.fixture
def admin_client(app: FastAPI, admin_user: CreatedUser) -> Iterator[TestClient]:
    """A client signed in as ``admin_user`` through the API, sending the CSRF token."""
    with TestClient(app) as client:
        assert sign_in(client, admin_user.email, admin_user.password).status_code == 200
        client.headers[CSRF_HEADER] = csrf_token(client)
        yield client
