"""Sign-up is rate-limited per client address (by default 10 attempts an hour, both
configurable), so it cannot be used to spam accounts, probe emails or burn CPU on
password hashing. Only that address waits."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.admin import SystemSettingsService
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token

SPAMMER = "203.0.113.66"
NEIGHBOUR = "198.51.100.7"
PASSWORD = "my own long passphrase"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Trust X-Forwarded-For from the test client, so a test can choose its address.
    return settings.model_copy(
        update={
            "forwarded_allow_ips": "*",
            "register_ip_max_attempts": 3,
            "register_ip_window_minutes": 60,
        }
    )


@pytest.fixture(autouse=True)
def open_registration(app: FastAPI, anonymous_client) -> None:
    SystemSettingsService(app.state.engine).set_registration(
        enabled=True, allowed_email_domains=["example.com"]
    )


def register(client: TestClient, n: int, ip: str = SPAMMER, domain: str = "example.com"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": f"user{n}@{domain}", "password": PASSWORD, "display_name": f"U{n}"},
        headers={CSRF_HEADER: csrf_token(client), "X-Forwarded-For": ip},
    )


def test_an_address_that_tried_too_often_must_wait(anonymous_client, clock: FakeClock):
    # Every attempt counts, whether it creates an account or is refused.
    first = [
        register(anonymous_client, 1).status_code,
        register(anonymous_client, 1).status_code,  # email taken
        register(anonymous_client, 2, domain="other.org").status_code,  # domain refused
    ]
    clock.advance(timedelta(minutes=10))

    fourth = register(anonymous_client, 3)

    assert first == [201, 409, 403]
    assert fourth.status_code == 429
    assert fourth.json()["error"]["code"] == "too_many_attempts"
    assert fourth.headers["retry-after"] == str(50 * 60)


def test_other_addresses_are_not_slowed(anonymous_client):
    for n in range(3):
        register(anonymous_client, n)

    assert register(anonymous_client, 9, ip=NEIGHBOUR).status_code == 201


def test_the_address_may_sign_up_again_once_the_window_has_passed(
    anonymous_client, clock: FakeClock
):
    for n in range(3):
        register(anonymous_client, n)

    clock.advance(timedelta(minutes=60))

    assert register(anonymous_client, 7).status_code == 201


def test_attempts_while_registration_is_closed_do_not_count(app: FastAPI, anonymous_client):
    SystemSettingsService(app.state.engine).set_registration(
        enabled=False, allowed_email_domains=[]
    )
    assert [register(anonymous_client, n).status_code for n in range(5)] == [403] * 5
    SystemSettingsService(app.state.engine).set_registration(enabled=True, allowed_email_domains=[])

    assert register(anonymous_client, 6).status_code == 201


def test_the_defaults_are_ten_attempts_an_hour(database_url: str):
    settings = Settings(database_url=database_url, encryption_key=b"k" * 32)  # type: ignore[arg-type]

    assert (settings.register_ip_max_attempts, settings.register_ip_window_minutes) == (10, 60)
