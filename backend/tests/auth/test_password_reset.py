"""A user who forgot their password asks for a reset link and sets a new password
(spec story 6, §6.1): the response is the same whether or not the email exists; the
link is single-use, valid 30 minutes and stored hashed; using it ends every session."""

import hashlib
import re
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.modules.auth import SecurityEventRecorder
from dawam.modules.mail import MailService
from dawam.platform.clock import FakeClock
from dawam.platform.config import Settings
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import EmailMessage, InMemoryOutbox
from tests.helpers import csrf_token, sign_in

FORGOT = "/api/v1/auth/password/forgot"
RESET = "/api/v1/auth/password/reset"
NEW_PASSWORD = "a brand new password"
SECOND = timedelta(seconds=1)
LINK = re.compile(r"(?P<url>http\S+/reset-password#token=(?P<token>[A-Za-z0-9_-]+))")


@pytest.fixture
def smtp(app, anonymous_client, outbox, clock) -> None:
    """SMTP is set up (after startup has migrated the database), so emails reach the outbox."""
    MailService(
        app.state.engine, app.state.settings, sender=outbox, clock=clock
    ).save_smtp_settings(
        host="smtp.example.com",
        port=587,
        security="starttls",
        sender="dawam@example.com",
        username=None,
    )


@pytest.fixture
def grace(create_user):
    return create_user(email="grace@example.com", password="correct horse battery")


def post(client: TestClient, path: str, body: dict):
    return client.post(path, json=body, headers={CSRF_HEADER: csrf_token(client)})


def forgot(client: TestClient, email: str):
    return post(client, FORGOT, {"email": email})


def reset(client: TestClient, token: str, password: str = NEW_PASSWORD):
    return post(client, RESET, {"token": token, "password": password})


def reset_email(outbox: InMemoryOutbox, address: str) -> EmailMessage:
    [message] = outbox.sent_to(address)
    return message


def token_in(message: EmailMessage) -> str:
    match = LINK.search(message.body)
    assert match, message.body
    return match["token"]


def request_token(client: TestClient, outbox: InMemoryOutbox, address: str) -> str:
    outbox.clear()
    assert forgot(client, address).status_code == 202
    return token_in(reset_email(outbox, address))


@pytest.mark.usefixtures("smtp")
def test_a_reset_link_is_emailed(anonymous_client, grace, outbox):
    response = forgot(anonymous_client, grace.email)

    assert response.status_code == 202
    message = reset_email(outbox, "grace@example.com")
    assert message.subject == "Reset your DAWAM password"
    assert "30 minutes" in message.body
    url = LINK.search(message.body)["url"]
    assert url.startswith("http://localhost:8000/reset-password#token=")


@pytest.mark.usefixtures("smtp")
def test_the_response_is_the_same_whether_or_not_the_email_exists(anonymous_client, grace, outbox):
    known = forgot(anonymous_client, grace.email)
    unknown = forgot(anonymous_client, "nobody@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    assert dict(known.headers).keys() == dict(unknown.headers).keys()
    assert outbox.sent_to("nobody@example.com") == []


@pytest.mark.usefixtures("smtp")
def test_the_email_is_matched_case_insensitively(anonymous_client, grace, outbox):
    forgot(anonymous_client, "  GRACE@example.com ")

    assert len(outbox.sent_to("grace@example.com")) == 1


@pytest.mark.usefixtures("smtp")
def test_the_link_sets_a_new_password(anonymous_client, grace, outbox):
    token = request_token(anonymous_client, outbox, grace.email)

    response = reset(anonymous_client, token)

    assert response.status_code == 204
    assert sign_in(anonymous_client, grace.email, NEW_PASSWORD).status_code == 200
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 401


@pytest.mark.usefixtures("smtp")
def test_a_link_works_only_once(anonymous_client, grace, outbox):
    token = request_token(anonymous_client, outbox, grace.email)
    reset(anonymous_client, token)

    again = reset(anonymous_client, token, "yet another password")

    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_reset_token"
    assert sign_in(anonymous_client, grace.email, NEW_PASSWORD).status_code == 200


@pytest.mark.usefixtures("smtp")
def test_a_link_works_for_30_minutes(anonymous_client, grace, outbox, clock: FakeClock):
    token = request_token(anonymous_client, outbox, grace.email)

    clock.advance(timedelta(minutes=30) - SECOND)

    assert reset(anonymous_client, token).status_code == 204


@pytest.mark.usefixtures("smtp")
def test_an_expired_link_is_rejected(anonymous_client, grace, outbox, clock: FakeClock):
    token = request_token(anonymous_client, outbox, grace.email)

    clock.advance(timedelta(minutes=30))

    response = reset(anonymous_client, token)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reset_token"
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 200


@pytest.mark.usefixtures("smtp")
def test_an_unknown_token_is_rejected(anonymous_client, grace):
    response = reset(anonymous_client, "not-a-real-token")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reset_token"


@pytest.mark.usefixtures("smtp")
def test_a_reset_ends_every_session_of_the_user(app, anonymous_client, grace, outbox):
    with TestClient(app) as laptop, TestClient(app) as phone:
        sign_in(laptop, grace.email, grace.password)
        sign_in(phone, grace.email, grace.password)
        token = request_token(anonymous_client, outbox, grace.email)

        reset(anonymous_client, token)

        assert laptop.get("/api/v1/me").status_code == 401
        assert phone.get("/api/v1/me").status_code == 401


@pytest.mark.usefixtures("smtp")
def test_a_reset_leaves_other_users_signed_in(anonymous_client, signed_in_client, grace, outbox):
    token = request_token(anonymous_client, outbox, grace.email)

    reset(anonymous_client, token)

    assert signed_in_client.get("/api/v1/me").status_code == 200


@pytest.mark.usefixtures("smtp")
def test_a_reset_voids_the_users_other_links(anonymous_client, grace, outbox):
    older = request_token(anonymous_client, outbox, grace.email)
    newer = request_token(anonymous_client, outbox, grace.email)

    assert reset(anonymous_client, newer).status_code == 204

    assert reset(anonymous_client, older, "yet another password").status_code == 400


@pytest.mark.usefixtures("smtp")
def test_a_password_that_breaks_the_policy_is_refused_and_the_link_still_works(
    anonymous_client, grace, outbox
):
    token = request_token(anonymous_client, outbox, grace.email)

    short = reset(anonymous_client, token, "short")

    assert short.status_code == 422
    assert short.json()["error"]["code"] == "invalid_password"
    assert reset(anonymous_client, token).status_code == 204


@pytest.mark.usefixtures("smtp")
def test_a_reset_unlocks_a_locked_account(anonymous_client, grace, outbox):
    """Whoever reads the user's email may choose their password, so the lock (there to
    stop password guessing) has nothing left to protect."""
    for _ in range(5):
        sign_in(anonymous_client, grace.email, "not the password")
    assert sign_in(anonymous_client, grace.email, grace.password).status_code == 429
    token = request_token(anonymous_client, outbox, grace.email)

    reset(anonymous_client, token)

    assert sign_in(anonymous_client, grace.email, NEW_PASSWORD).status_code == 200


@pytest.mark.usefixtures("smtp")
def test_requesting_and_using_a_link_are_security_events(
    app, anonymous_client, grace, outbox, clock
):
    token = request_token(anonymous_client, outbox, grace.email)
    forgot(anonymous_client, "nobody@example.com")
    reset(anonymous_client, token)

    used, requested = SecurityEventRecorder(app.state.engine, clock=clock).recent()[:2]

    assert requested.event_type == "password_reset_requested"
    assert used.event_type == "password_reset"
    for event in (requested, used):
        assert (event.actor_id, event.target_type, event.target_id) == (grace.id, "user", grace.id)
        assert event.ip == "testclient"
        assert token not in str(event.metadata)


@pytest.mark.usefixtures("smtp")
def test_reset_tokens_are_stored_only_hashed(app, anonymous_client, grace, outbox):
    """Storage-property check: reads the auth module's own table."""
    token = request_token(anonymous_client, outbox, grace.email)

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM password_resets")).mappings().all()

    assert len(rows) == 1
    assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert all(token not in str(value) for value in rows[0].values())


def test_the_link_points_at_dawam_public_url(settings, services, create_user, outbox):
    settings = settings.model_copy(update={"public_url": "https://dawam.example.com"})
    with TestClient(create_app(settings, services=services)) as client:
        app = client.app
        MailService(
            app.state.engine, settings, sender=outbox, clock=services.clock
        ).save_smtp_settings(
            host="smtp.example.com", port=25, security="none", sender="d@example.com", username=None
        )
        create_user(email="grace@example.com")

        forgot(client, "grace@example.com")

    body = reset_email(outbox, "grace@example.com").body
    assert "https://dawam.example.com/reset-password#token=" in body


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://dawam.example.com/", "https://dawam.example.com"),
        ("http://10.0.0.5:8000", "http://10.0.0.5:8000"),
        ("https://example.com/dawam/", "https://example.com/dawam"),
    ],
)
def test_public_url_is_kept_without_a_trailing_slash(settings, value, expected):
    parsed = Settings.model_validate({**settings.model_dump(), "public_url": value})

    assert parsed.public_url == expected


@pytest.mark.parametrize("value", ["dawam.example.com", "ftp://dawam.example.com", "https://"])
def test_public_url_must_be_an_http_url(settings, value):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), "public_url": value})
