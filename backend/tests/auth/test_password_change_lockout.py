"""Guessing the current password through the password change counts against the
account like failed sign-ins do (spec §6.1 lockout), so a hijacked session cannot
brute-force it."""

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.auth import SecurityEventRecorder
from dawam.platform.clock import FakeClock
from tests.helpers import sign_in

NEW_PASSWORD = "a brand new passphrase"
WRONG = "not my password"


def change_password(client: TestClient, current: str, new: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/password/change",
        json={"current_password": current, "new_password": new},
    )


def events(app: FastAPI, clock: FakeClock, event_type: str):
    recorder = SecurityEventRecorder(app.state.engine, clock=clock)
    return [e for e in recorder.recent() if e.event_type == event_type]


def test_five_wrong_current_passwords_lock_the_account(
    signed_in_client, signed_in_user, anonymous_client
):
    statuses = [change_password(signed_in_client, WRONG).status_code for _ in range(4)]
    fifth = change_password(signed_in_client, WRONG)

    assert statuses == [400] * 4
    assert fifth.status_code == 429
    assert fifth.json()["error"]["code"] == "account_locked"
    assert fifth.headers["retry-after"] == "900"
    # Locked: neither the right current password nor a sign-in gets through.
    right = change_password(signed_in_client, signed_in_user.password)
    assert (right.status_code, right.json()["error"]["code"]) == (429, "account_locked")
    signing_in = sign_in(anonymous_client, signed_in_user.email, signed_in_user.password)
    assert signing_in.json()["error"]["code"] == "account_locked"


def test_the_lock_ends_after_the_lockout(signed_in_client, signed_in_user, clock: FakeClock):
    for _ in range(5):
        change_password(signed_in_client, WRONG)

    clock.advance(timedelta(minutes=15))

    assert change_password(signed_in_client, signed_in_user.password).status_code == 204


def test_failed_sign_ins_and_wrong_current_passwords_add_up(
    signed_in_client, signed_in_user, anonymous_client
):
    for _ in range(3):
        sign_in(anonymous_client, signed_in_user.email, WRONG)
    assert change_password(signed_in_client, WRONG).status_code == 400

    assert change_password(signed_in_client, WRONG).status_code == 429


def test_a_successful_change_ends_the_streak(signed_in_client, signed_in_user):
    for _ in range(4):
        change_password(signed_in_client, WRONG)
    assert change_password(signed_in_client, signed_in_user.password).status_code == 204

    assert [change_password(signed_in_client, WRONG).status_code for _ in range(4)] == [400] * 4


def test_each_wrong_current_password_is_a_security_event(
    app, signed_in_client, signed_in_user, clock: FakeClock
):
    for _ in range(5):
        change_password(signed_in_client, WRONG)
    change_password(signed_in_client, signed_in_user.password)  # refused: locked

    failed = events(app, clock, "password_change_failed")
    assert len(failed) == 6
    assert {e.metadata["reason"] for e in failed} == {"wrong_password", "account_locked"}
    for event in failed:
        assert (event.actor_id, event.target_id, event.ip) == (
            signed_in_user.id,
            signed_in_user.id,
            "testclient",
        )
        assert WRONG not in str(event.metadata)
        assert signed_in_user.password not in str(event.metadata)
    [locked] = events(app, clock, "account_locked")
    assert locked.target_id == signed_in_user.id
