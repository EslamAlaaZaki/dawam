"""Sign-in attempts are rate-limited per IP: after 20 failures from one address within
15 minutes (both configurable) that address must wait, and only that address
(spec story 9, §6.1 rate limiting)."""

from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token

SECOND = timedelta(seconds=1)
WINDOW = timedelta(minutes=15)
ATTACKER = "203.0.113.66"
NEIGHBOUR = "198.51.100.7"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Trust X-Forwarded-For from the test client, so a test can choose its address.
    return settings.model_copy(update={"forwarded_allow_ips": "*"})


def attempt(client: TestClient, email: str, password: str, ip: str):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={CSRF_HEADER: csrf_token(client), "X-Forwarded-For": ip},
    )


def spray(client: TestClient, times: int, ip: str = ATTACKER, start: int = 0) -> list:
    """Failed sign-ins on as many different emails (so no account locks)."""
    return [
        attempt(client, f"guess{i}@example.com", "a wrong password", ip)
        for i in range(start, start + times)
    ]


@pytest.fixture
def grace(create_user):
    return create_user(email="grace@example.com")


def test_twenty_failures_from_one_address_are_still_answered(anonymous_client):
    responses = spray(anonymous_client, 20)

    assert {r.status_code for r in responses} == {401}


def test_the_next_attempt_from_that_address_must_wait(anonymous_client, grace):
    spray(anonymous_client, 20)

    response = attempt(anonymous_client, grace.email, grace.password, ATTACKER)

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "too_many_attempts"
    assert "15 minutes" in error["message"]
    assert error["details"] == {"retry_after_seconds": 900}
    assert response.headers["retry-after"] == "900"
    assert "dawam_session" not in response.cookies


def test_other_addresses_are_not_slowed_down(anonymous_client, grace):
    spray(anonymous_client, 25)

    assert attempt(anonymous_client, grace.email, grace.password, NEIGHBOUR).status_code == 200


def test_the_limit_counts_failures_in_the_last_15_minutes(anonymous_client, clock: FakeClock):
    spray(anonymous_client, 10)
    clock.advance(timedelta(minutes=10))
    spray(anonymous_client, 10, start=10)
    clock.advance(timedelta(minutes=5) - SECOND)

    response = spray(anonymous_client, 1, start=20)[0]
    assert response.status_code == 429
    assert response.json()["error"]["details"] == {"retry_after_seconds": 1}

    clock.advance(SECOND)  # the first ten are now older than 15 minutes
    assert {r.status_code for r in spray(anonymous_client, 10, start=30)} == {401}
    assert spray(anonymous_client, 1, start=40)[0].status_code == 429


def test_waiting_attempts_neither_count_nor_touch_any_account(
    anonymous_client, grace, clock: FakeClock
):
    spray(anonymous_client, 20)
    for _ in range(10):
        assert (
            attempt(anonymous_client, grace.email, "a wrong password", ATTACKER).status_code == 429
        )

    # Grace's account was never tried, so it did not lock...
    assert attempt(anonymous_client, grace.email, grace.password, NEIGHBOUR).status_code == 200
    # ...and the address waited only until its first twenty failures left the window.
    clock.advance(WINDOW)
    assert spray(anonymous_client, 1, start=20)[0].status_code == 401


def test_successful_sign_ins_do_not_count(anonymous_client, grace):
    for _ in range(25):
        assert attempt(anonymous_client, grace.email, grace.password, ATTACKER).status_code == 200


def test_attempts_refused_by_an_account_lock_count_too(anonymous_client, grace):
    # Five lock Grace's account; the other fifteen are refused by that lock.
    codes = [
        attempt(anonymous_client, grace.email, "a wrong password", ATTACKER).json()["error"]["code"]
        for _ in range(20)
    ]
    assert codes[-1] == "account_locked"

    assert spray(anonymous_client, 1)[0].json()["error"]["code"] == "too_many_attempts"


def test_the_address_limit_is_configurable(settings, services, grace, clock: FakeClock):
    settings = settings.model_copy(
        update={"login_ip_max_failures": 3, "login_ip_window_minutes": 1}
    )

    with TestClient(create_app(settings, services=services)) as client:
        assert {r.status_code for r in spray(client, 3)} == {401}
        response = attempt(client, grace.email, grace.password, ATTACKER)
        assert response.json()["error"]["code"] == "too_many_attempts"
        assert response.headers["retry-after"] == "60"

        clock.advance(timedelta(minutes=1))
        assert attempt(client, grace.email, grace.password, ATTACKER).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"), [("login_ip_max_failures", 0), ("login_ip_window_minutes", 0)]
)
def test_address_limit_settings_must_be_positive(settings, field, value):
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), field: value})


def test_failures_older_than_the_window_are_not_kept(
    app: FastAPI, anonymous_client, clock: FakeClock
):
    """A deliberate storage-property check: it reads the auth tables directly."""
    spray(anonymous_client, 10)
    spray(anonymous_client, 5, ip=NEIGHBOUR, start=10)
    clock.advance(WINDOW)

    spray(anonymous_client, 1, start=20)

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT ip, failed_at FROM login_failures")).all()
    assert rows == [(ATTACKER, clock())]
