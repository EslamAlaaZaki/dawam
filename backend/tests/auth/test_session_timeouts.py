"""Sessions end after an idle timeout (default 8 h) and an absolute timeout (default 14 days),
both configurable (spec story 3, §6.1)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from tests.helpers import cookie_attributes, set_cookie_headers, sign_in

SECOND = timedelta(seconds=1)


def me_status(client: TestClient) -> int:
    return client.get("/api/v1/me").status_code


@pytest.fixture
def grace(create_user):
    return create_user()


def test_a_session_survives_just_under_the_idle_timeout(anonymous_client, grace, clock: FakeClock):
    sign_in(anonymous_client, grace.email, grace.password)

    clock.advance(timedelta(hours=8) - SECOND)

    assert me_status(anonymous_client) == 200


def test_an_idle_session_ends_after_8_hours(anonymous_client, grace, clock: FakeClock):
    sign_in(anonymous_client, grace.email, grace.password)

    clock.advance(timedelta(hours=8) + SECOND)

    assert me_status(anonymous_client) == 401
    clock.advance(-timedelta(hours=1))  # an expired session is gone for good
    assert me_status(anonymous_client) == 401


def test_activity_keeps_a_session_alive(anonymous_client, grace, clock: FakeClock):
    sign_in(anonymous_client, grace.email, grace.password)

    for _ in range(5):
        clock.advance(timedelta(hours=7))
        assert me_status(anonymous_client) == 200


def test_an_active_session_still_ends_after_14_days(anonymous_client, grace, clock: FakeClock):
    sign_in(anonymous_client, grace.email, grace.password)
    for _ in range(47):  # 47 * 7 h = 13 days 17 h
        clock.advance(timedelta(hours=7))
        assert me_status(anonymous_client) == 200

    clock.advance(timedelta(hours=7))  # exactly 14 days after signing in

    assert me_status(anonymous_client) == 401


def test_timeouts_are_configurable(settings, services, create_user, clock: FakeClock):
    grace = create_user()
    settings = settings.model_copy(
        update={"session_idle_timeout_hours": 1, "session_absolute_timeout_days": 0.5}
    )

    with TestClient(create_app(settings, services=services)) as client:
        response = sign_in(client, grace.email, grace.password)
        assert response.status_code == 200
        clock.advance(timedelta(hours=1) - SECOND)
        assert me_status(client) == 200
        clock.advance(timedelta(hours=1) + SECOND)
        assert me_status(client) == 401

        sign_in(client, grace.email, grace.password)
        for _ in range(11):
            clock.advance(timedelta(minutes=59))
            assert me_status(client) == 200
        clock.advance(timedelta(minutes=59))  # 11 h 48 min in
        assert me_status(client) == 200
        clock.advance(timedelta(minutes=13))  # 12 h 1 min in: past the absolute timeout
        assert me_status(client) == 401

    [cookie] = set_cookie_headers(response, "dawam_session")
    assert cookie_attributes(cookie)["max-age"] == "43200"


@pytest.mark.parametrize("field", ["session_idle_timeout_hours", "session_absolute_timeout_days"])
def test_timeouts_must_be_positive(settings, field):
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), field: 0})
