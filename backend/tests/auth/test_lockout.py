"""After 5 consecutive failed sign-ins an account locks for 15 minutes, both configurable
(spec story 9, §6.1 rate limiting; §10 auth flows: lockout)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.modules.auth import AuthService, SecurityEventRecorder
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from tests.helpers import set_cookie_headers, sign_in

SECOND = timedelta(seconds=1)
LOCKOUT = timedelta(minutes=15)
WRONG = "not the password"


@pytest.fixture
def grace(create_user):
    return create_user(email="grace@example.com")


@pytest.fixture
def recorder(app: FastAPI, anonymous_client, clock: FakeClock) -> SecurityEventRecorder:
    return SecurityEventRecorder(app.state.engine, clock=clock)


def fail(client: TestClient, email: str, times: int) -> list:
    return [sign_in(client, email, WRONG) for _ in range(times)]


def code_of(response) -> str:
    return response.json()["error"]["code"]


def test_four_failures_do_not_lock_the_account(anonymous_client, grace):
    responses = fail(anonymous_client, grace.email, 4)

    assert [r.status_code for r in responses] == [401] * 4
    assert {code_of(r) for r in responses} == {"invalid_credentials"}
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200


def test_the_fifth_consecutive_failure_locks_the_account(anonymous_client, grace):
    *_, fifth = fail(anonymous_client, grace.email, 5)

    assert fifth.status_code == 429
    error = fifth.json()["error"]
    assert error["code"] == "account_locked"
    assert "15 minutes" in error["message"]
    assert error["details"] == {"retry_after_seconds": 900}
    assert fifth.headers["retry-after"] == "900"


def test_a_correct_password_is_rejected_while_the_account_is_locked(
    anonymous_client, grace, clock: FakeClock
):
    fail(anonymous_client, grace.email, 5)
    clock.advance(LOCKOUT - SECOND)

    response = sign_in(anonymous_client, grace.email, grace.password)

    assert response.status_code == 429
    assert code_of(response) == "account_locked"
    assert response.json()["error"]["details"] == {"retry_after_seconds": 1}
    assert set_cookie_headers(response, "dawam_session") == []
    assert anonymous_client.get("/api/v1/me").status_code == 401


def test_the_account_unlocks_after_15_minutes(anonymous_client, grace, clock: FakeClock):
    fail(anonymous_client, grace.email, 5)
    clock.advance(LOCKOUT)

    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200


def test_attempts_during_the_lock_do_not_extend_it(anonymous_client, grace, clock: FakeClock):
    fail(anonymous_client, grace.email, 5)
    clock.advance(timedelta(minutes=10))
    fail(anonymous_client, grace.email, 3)
    clock.advance(timedelta(minutes=5))

    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200


def test_a_successful_sign_in_resets_the_failure_count(anonymous_client, grace):
    fail(anonymous_client, grace.email, 4)
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200

    responses = fail(anonymous_client, grace.email, 4)

    assert {r.status_code for r in responses} == {401}
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200


def test_after_a_lock_ends_the_count_starts_again(anonymous_client, grace, clock: FakeClock):
    fail(anonymous_client, grace.email, 5)
    clock.advance(LOCKOUT)

    *first_four, fifth = fail(anonymous_client, grace.email, 5)

    assert {r.status_code for r in first_four} == {401}
    assert code_of(fifth) == "account_locked"


def test_failures_count_however_far_apart_they_are(anonymous_client, grace, clock: FakeClock):
    for _ in range(4):
        fail(anonymous_client, grace.email, 1)
        clock.advance(timedelta(days=1))

    [fifth] = fail(anonymous_client, grace.email, 1)

    assert code_of(fifth) == "account_locked"


def test_the_lock_is_per_account(anonymous_client, grace, create_user):
    ada = create_user(email="ada@example.com", password="analytical engine")
    fail(anonymous_client, grace.email, 5)

    assert sign_in(anonymous_client, ada.email, ada.password).status_code == 200


def test_the_lock_holds_whatever_case_the_email_is_typed_in(anonymous_client, grace):
    fail(anonymous_client, "GRACE@example.com", 5)

    response = sign_in(anonymous_client, " grace@EXAMPLE.com", grace.password)

    assert code_of(response) == "account_locked"


def test_an_unknown_email_locks_just_like_an_account(anonymous_client, grace, clock: FakeClock):
    """Locking must not reveal which emails have accounts."""

    def story(email: str) -> list:
        responses = fail(anonymous_client, email, 5)
        clock.advance(timedelta(minutes=5))
        responses += fail(anonymous_client, email, 1)
        clock.advance(timedelta(minutes=10))
        responses += fail(anonymous_client, email, 5)
        clock.advance(LOCKOUT)
        return [(r.status_code, r.json(), r.headers.get("retry-after")) for r in responses]

    assert story("nobody@example.com") == story(grace.email)


def test_the_lockout_is_recorded_as_a_security_event(
    anonymous_client, grace, recorder: SecurityEventRecorder, clock: FakeClock
):
    fail(anonymous_client, grace.email, 5)
    sign_in(anonymous_client, grace.email, grace.password)

    during_lock, locked, fifth_failure, *earlier = recorder.recent()
    assert len(earlier) == 4
    assert locked.event_type == "account_locked"
    assert locked.actor_id is None
    assert (locked.target_type, locked.target_id) == ("user", grace.id)
    assert locked.metadata == {
        "email": grace.email,
        "failed_attempts": 5,
        "locked_until": (clock() + LOCKOUT).isoformat(),
    }
    assert (locked.ip, locked.created_at) == ("testclient", clock())
    assert fifth_failure.event_type == "login_failed"
    assert fifth_failure.metadata["reason"] == "invalid_credentials"
    # A correct password during the lock is a failed sign-in too.
    assert during_lock.event_type == "login_failed"
    assert during_lock.metadata == {"email": grace.email, "reason": "account_locked"}


def test_the_lockout_threshold_and_duration_are_configurable(
    settings, services, create_user, clock: FakeClock
):
    grace = create_user()
    settings = settings.model_copy(update={"login_max_failures": 3, "login_lockout_minutes": 2})

    with TestClient(create_app(settings, services=services)) as client:
        *first_two, third = fail(client, grace.email, 3)
        assert {r.status_code for r in first_two} == {401}
        assert code_of(third) == "account_locked"
        assert "2 minutes" in third.json()["error"]["message"]
        assert third.headers["retry-after"] == "120"

        clock.advance(timedelta(minutes=2) - SECOND)
        assert code_of(sign_in(client, grace.email, grace.password)) == "account_locked"
        clock.advance(SECOND)
        assert sign_in(client, grace.email, grace.password).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"), [("login_max_failures", 0), ("login_lockout_minutes", 0)]
)
def test_lockout_settings_must_be_positive(settings, field, value):
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), field: value})


def test_the_lock_does_not_change_the_users_last_login(
    anonymous_client, grace, auth_service: AuthService
):
    fail(anonymous_client, grace.email, 5)
    sign_in(anonymous_client, grace.email, grace.password)

    assert auth_service.get_user(grace.id).last_login_at is None
