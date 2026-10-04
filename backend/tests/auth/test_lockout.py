"""After 5 consecutive failed sign-ins an account locks for 15 minutes, both configurable
(spec story 9, §6.1 rate limiting; §10 auth flows: lockout)."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.modules.auth import AuthService, SecurityEventRecorder
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.errors import ApiError
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


def test_failures_up_to_a_day_apart_add_up(anonymous_client, grace, clock: FakeClock):
    for _ in range(4):
        fail(anonymous_client, grace.email, 1)
        clock.advance(timedelta(hours=24) - SECOND)

    [fifth] = fail(anonymous_client, grace.email, 1)

    assert code_of(fifth) == "account_locked"


def test_failures_older_than_a_day_are_forgotten(anonymous_client, grace, clock: FakeClock):
    fail(anonymous_client, grace.email, 4)
    clock.advance(timedelta(hours=24))

    responses = fail(anonymous_client, grace.email, 4)

    assert {r.status_code for r in responses} == {401}
    assert code_of(fail(anonymous_client, grace.email, 1)[0]) == "account_locked"


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
        responses += fail(anonymous_client, email, 3)
        clock.advance(timedelta(hours=24))  # forgotten...
        responses += fail(anonymous_client, email, 4)
        clock.advance(timedelta(hours=23))  # ...remembered
        responses += fail(anonymous_client, email, 1)
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


@pytest.mark.parametrize("email", ["grace@example.com", "nobody@example.com"])
def test_concurrent_failures_cannot_skip_the_lock(auth_service: AuthService, grace, email):
    """Ten simultaneous wrong guesses: exactly five are checked, the fifth locks."""

    def guess(_: int) -> str:
        try:
            auth_service.sign_in(email, WRONG)
        except ApiError as exc:
            return exc.code
        return "signed_in"

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = Counter(pool.map(guess, range(10)))

    assert codes == {"invalid_credentials": 4, "account_locked": 6}


def test_a_prune_racing_an_unknown_email_attempt_cannot_take_its_row(
    app: FastAPI, anonymous_client, clock: FakeClock
):
    """A deliberate concurrency check: it reads the auth tables directly.

    The email's row is stale (prunable). Right after the attempt's first statement on
    ``login_lockouts``, another connection prunes as ``prune`` does (``SKIP LOCKED``).
    The attempt must already hold the row, so the prune skips it."""
    fail(anonymous_client, "nobody@example.com", 1)
    clock.advance(timedelta(hours=24))
    engine = app.state.engine
    pruned: list[int] = []

    def prune_concurrently(conn, cursor, statement, *args) -> None:
        if pruned or not statement.lstrip().upper().startswith("INSERT INTO LOGIN_LOCKOUTS"):
            return
        with engine.connect() as other, other.begin():
            result = other.execute(
                sa.text(
                    "DELETE FROM login_lockouts WHERE email_key IN "
                    "(SELECT email_key FROM login_lockouts FOR UPDATE SKIP LOCKED)"
                )
            )
            pruned.append(result.rowcount)

    sa.event.listen(engine, "after_cursor_execute", prune_concurrently)
    try:
        response = sign_in(anonymous_client, "nobody@example.com", WRONG)
    finally:
        sa.event.remove(engine, "after_cursor_execute", prune_concurrently)

    assert pruned == [0]
    assert response.status_code == 401


def test_an_unknown_emails_lock_state_is_kept_by_hash_and_not_for_ever(
    app: FastAPI, anonymous_client, clock: FakeClock
):
    """A deliberate storage-property check: it reads the auth tables directly."""
    fail(anonymous_client, "nobody@example.com", 2)

    def rows() -> list:
        with app.state.engine.connect() as conn:
            return conn.execute(sa.text("SELECT * FROM login_lockouts")).mappings().all()

    [row] = rows()
    assert "nobody@example.com" not in {str(value) for value in row.values()}

    clock.advance(timedelta(hours=24))
    fail(anonymous_client, "someone@example.com", 1)

    assert len(rows()) == 1  # nobody@'s forgotten failures are gone
