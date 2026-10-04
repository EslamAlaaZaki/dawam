"""Security events: sign-ins are recorded, and other modules record theirs through
``SecurityEventRecorder`` (spec stories 9 and 23, §6.1, §7 ``SecurityEvent``)."""

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session

from dawam.modules.auth import SecurityEvent, SecurityEventRecorder
from dawam.platform.clock import FakeClock
from tests.helpers import sign_in


@pytest.fixture
def recorder(app: FastAPI, anonymous_client, clock: FakeClock) -> SecurityEventRecorder:
    """The auth module's recorder on the test app (startup has migrated the database)."""
    return SecurityEventRecorder(app.state.engine, clock=clock)


def test_a_successful_sign_in_is_recorded_with_actor_ip_and_time(
    anonymous_client, create_user, recorder: SecurityEventRecorder, clock: FakeClock
):
    grace = create_user()

    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200

    [event] = recorder.recent()
    assert event == SecurityEvent(
        id=event.id,
        event_type="login_succeeded",
        actor_id=grace.id,
        target_type="user",
        target_id=grace.id,
        metadata={"email": grace.email},
        ip="testclient",
        created_at=clock(),
    )


def test_a_wrong_password_is_recorded_against_the_account_without_an_actor(
    anonymous_client, create_user, recorder: SecurityEventRecorder, clock: FakeClock
):
    grace = create_user()

    assert sign_in(anonymous_client, grace.email, "not the password").status_code == 401

    [event] = recorder.recent()
    assert event.event_type == "login_failed"
    assert event.actor_id is None  # nobody proved who they are
    assert (event.target_type, event.target_id) == ("user", grace.id)
    assert event.metadata == {"email": grace.email, "reason": "invalid_credentials"}
    assert (event.ip, event.created_at) == ("testclient", clock())


def test_a_failed_sign_in_for_an_unknown_email_is_recorded_too(
    anonymous_client, recorder: SecurityEventRecorder
):
    assert sign_in(anonymous_client, " Nobody@Example.com", "whatever it is").status_code == 401

    [event] = recorder.recent()
    assert event.event_type == "login_failed"
    assert (event.actor_id, event.target_type, event.target_id) == (None, None, None)
    assert event.metadata == {"email": "nobody@example.com", "reason": "invalid_credentials"}
    assert event.ip == "testclient"


def test_no_security_event_holds_a_password(app: FastAPI, anonymous_client, create_user):
    """A deliberate storage-property check: it reads the auth tables directly."""
    grace = create_user(password="correct horse battery")
    sign_in(anonymous_client, grace.email, "a wrong horse battery")
    sign_in(anonymous_client, "nobody@example.com", "an unknown horse battery")
    sign_in(anonymous_client, grace.email, "correct horse battery")

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM security_events")).mappings().all()
    assert len(rows) == 3
    stored = " ".join(str(value) for row in rows for value in row.values())
    assert "horse battery" not in stored


def test_other_modules_record_events_through_the_recorder(
    recorder: SecurityEventRecorder, clock: FakeClock
):
    admin_id, user_id = uuid.uuid4(), uuid.uuid4()

    recorded = recorder.record(
        "role_changed",
        actor_id=admin_id,
        target_type="user",
        target_id=user_id,
        metadata={"from": "user", "to": "admin"},
        ip="203.0.113.7",
    )

    assert recorded == SecurityEvent(
        id=recorded.id,
        event_type="role_changed",
        actor_id=admin_id,
        target_type="user",
        target_id=user_id,
        metadata={"from": "user", "to": "admin"},
        ip="203.0.113.7",
        created_at=clock(),
    )
    assert recorder.recent() == [recorded]


def test_recent_lists_the_newest_first(recorder: SecurityEventRecorder, clock: FakeClock):
    first = recorder.record("workspace_deleted")
    clock.advance(timedelta(seconds=1))
    second = recorder.record("user_deactivated")

    assert recorder.recent() == [second, first]
    assert recorder.recent(limit=1) == [second]


def test_an_event_recorded_in_the_callers_transaction_rolls_back_with_it(
    app: FastAPI, recorder: SecurityEventRecorder
):
    with Session(app.state.engine) as db, db.begin():
        recorder.record("ownership_reassigned", db=db)
    with pytest.raises(RuntimeError), Session(app.state.engine) as db, db.begin():
        recorder.record("user_deactivated", db=db)
        raise RuntimeError("the caller's own change failed")

    assert [event.event_type for event in recorder.recent()] == ["ownership_reassigned"]


@pytest.mark.parametrize("key", ["password", "new_password", "reset_token", "client_secret"])
def test_the_recorder_refuses_metadata_that_looks_like_a_secret(
    recorder: SecurityEventRecorder, key: str
):
    with pytest.raises(ValueError, match="secret"):
        recorder.record("password_changed", metadata={key: "x"})

    assert recorder.recent() == []


@pytest.mark.parametrize("event_type", ["", "Login", "login failed", "x" * 65])
def test_event_types_are_short_snake_case_names(recorder: SecurityEventRecorder, event_type):
    with pytest.raises(ValueError):
        recorder.record(event_type)
