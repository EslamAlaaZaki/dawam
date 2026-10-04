"""X-Forwarded-For / X-Forwarded-Proto count only from a trusted hop.

In Compose the one trusted hop is the `edge` proxy, which overwrites both headers;
``DAWAM_FORWARDED_ALLOW_IPS`` names it. A request from any other address keeps its
own address and scheme, whatever headers it sends.
"""

import pytest
from fastapi.testclient import TestClient

from dawam.modules.auth import AuthService
from dawam.platform.config import Settings
from tests.helpers import sign_in

EDGE = "10.0.0.2"
FORWARDED_FOR = {"X-Forwarded-For": "203.0.113.9"}
FORWARDED_HTTPS = {"X-Forwarded-Proto": "https"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"forwarded_allow_ips": EDGE})


def client_from(app, address: str, headers: dict[str, str]) -> TestClient:
    return TestClient(app, client=(address, 41234), headers=headers)


def test_the_session_records_the_client_the_trusted_hop_names(
    app, anonymous_client, create_user, auth_service: AuthService
):
    grace = create_user()

    with client_from(app, EDGE, FORWARDED_FOR) as client:
        response = sign_in(client, grace.email, grace.password)

    assert response.status_code == 200
    [session] = auth_service.sessions_of(grace.id)
    assert session.ip == "203.0.113.9"


def test_forwarded_headers_from_anyone_else_are_ignored(
    app, anonymous_client, create_user, auth_service: AuthService
):
    grace = create_user()

    with client_from(app, "10.0.0.9", FORWARDED_FOR | FORWARDED_HTTPS) as client:
        response = sign_in(client, grace.email, grace.password)

    assert response.status_code == 200
    [session] = auth_service.sessions_of(grace.id)
    assert session.ip == "10.0.0.9"
    assert "Strict-Transport-Security" not in response.headers
    assert "secure" not in response.headers["set-cookie"].lower()


def test_https_counts_only_when_the_trusted_hop_says_so(app, anonymous_client):
    with client_from(app, EDGE, FORWARDED_HTTPS) as client:
        response = client.get("/api/v1/version")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert "secure" in response.headers["set-cookie"].lower()  # the new CSRF cookie
