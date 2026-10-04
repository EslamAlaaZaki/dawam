"""Forgot-password cannot be used to flood a mailbox or the database: at most one reset
email per account per cooldown, and a cap per client address. A throttled request
gets the same answer as any other, and leaves no email and no reset row."""

import logging
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.modules.mail import MailService
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token

FORGOT = "/api/v1/auth/password/forgot"
SECOND = timedelta(seconds=1)


def save_smtp(app, outbox, clock) -> None:
    MailService(
        app.state.engine, app.state.settings, sender=outbox, clock=clock
    ).save_smtp_settings(
        host="smtp.example.com", port=25, security="none", sender="d@example.com", username=None
    )


@pytest.fixture
def smtp(app, anonymous_client, outbox, clock) -> None:
    save_smtp(app, outbox, clock)


def forgot(client: TestClient, email: str):
    return client.post(FORGOT, json={"email": email}, headers={CSRF_HEADER: csrf_token(client)})


def reset_rows(app) -> int:
    """Storage-property check: reads the auth module's own table."""
    with app.state.engine.connect() as conn:
        return conn.scalar(sa.text("SELECT count(*) FROM password_resets"))


@pytest.mark.usefixtures("smtp")
def test_one_reset_email_per_account_per_cooldown(
    app, anonymous_client, create_user, outbox, clock, caplog
):
    grace = create_user(email="grace@example.com")
    first = forgot(anonymous_client, grace.email)

    with caplog.at_level(logging.WARNING, logger="dawam.modules.auth.service"):
        again = forgot(anonymous_client, grace.email)

    assert first.status_code == again.status_code == 202
    assert first.content == again.content == b""
    assert len(outbox.sent_to(grace.email)) == 1
    assert reset_rows(app) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["password reset request throttled"]


@pytest.mark.usefixtures("smtp")
def test_after_the_cooldown_another_email_is_sent(anonymous_client, create_user, outbox, clock):
    grace = create_user(email="grace@example.com")
    forgot(anonymous_client, grace.email)

    clock.advance(timedelta(minutes=5) - SECOND)
    forgot(anonymous_client, grace.email)
    clock.advance(SECOND)
    forgot(anonymous_client, grace.email)

    assert len(outbox.sent_to(grace.email)) == 2


@pytest.mark.usefixtures("smtp")
def test_the_cooldown_is_per_account(anonymous_client, create_user, outbox):
    for name in ("grace", "ada"):
        create_user(email=f"{name}@example.com")

    forgot(anonymous_client, "grace@example.com")
    forgot(anonymous_client, "ada@example.com")

    assert len(outbox.messages) == 2


def test_a_client_address_is_capped(settings, services, create_user, outbox, clock):
    settings = settings.model_copy(
        update={"password_reset_ip_max_requests": 2, "password_reset_ip_window_minutes": 60}
    )
    with TestClient(create_app(settings, services=services)) as client:
        app = client.app
        save_smtp(app, outbox, clock)
        for name in ("a", "b", "c"):
            create_user(email=f"{name}@example.com")

        responses = [forgot(client, f"{name}@example.com") for name in ("a", "b", "c")]
        clock.advance(timedelta(minutes=60))
        later = forgot(client, "c@example.com")

        assert {r.status_code for r in [*responses, later]} == {202}
        assert [m.to for m in outbox.messages] == [
            "a@example.com",
            "b@example.com",
            "c@example.com",
        ]
        assert reset_rows(app) == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [("password_reset_cooldown_minutes", 0), ("password_reset_ip_max_requests", 0)],
)
def test_limits_must_be_positive(settings, field, value):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), field: value})
