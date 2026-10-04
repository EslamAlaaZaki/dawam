"""An admin configures SMTP and sends a test email; the SMTP password is stored encrypted
and never returned (spec story 24, §6.3)."""

import pytest
import sqlalchemy as sa

from dawam.modules.auth import SecurityEventRecorder
from dawam.platform.crypto import SecretBox
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import InMemoryOutbox, SmtpConfig
from tests.conftest import TEST_ENCRYPTION_KEY
from tests.helpers import csrf_token

SMTP = "/api/v1/admin/smtp"
SETTINGS = {
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "username": "dawam",
    "password": "smtp password 1",
    "sender": "dawam@example.com",
}


def stored_password(app) -> str | None:
    """Storage-property check: reads the mail module's own table."""
    with app.state.engine.connect() as conn:
        return conn.scalar(sa.text("SELECT password_encrypted FROM smtp_settings"))


def save(client, **changes):
    return client.put(SMTP, json={**SETTINGS, **changes})


def test_smtp_is_not_configured_at_first(admin_client):
    response = admin_client.get(SMTP)

    assert response.status_code == 200
    assert response.json() is None


def test_an_admin_saves_settings_and_the_password_is_never_returned(admin_client):
    saved = save(admin_client)

    assert saved.status_code == 200
    expected = {
        "host": "smtp.example.com",
        "port": 587,
        "security": "starttls",
        "username": "dawam",
        "has_password": True,
        "sender": "dawam@example.com",
    }
    assert {k: v for k, v in saved.json().items() if k != "updated_at"} == expected
    assert saved.json()["updated_at"]
    fetched = admin_client.get(SMTP)
    assert fetched.json() == saved.json()
    for response in (saved, fetched):
        assert "smtp password 1" not in response.text
        assert "password_encrypted" not in response.text


def test_the_password_is_stored_encrypted_with_aes_gcm(app, admin_client):
    save(admin_client)

    sealed = stored_password(app)

    assert sealed is not None
    assert "smtp password 1" not in sealed
    assert SecretBox(TEST_ENCRYPTION_KEY).decrypt(sealed, context="smtp.password") == (
        "smtp password 1"
    )


def test_saving_without_a_password_keeps_the_stored_one(app, admin_client, outbox):
    save(admin_client)
    settings = {k: v for k, v in SETTINGS.items() if k != "password"}

    response = admin_client.put(
        SMTP, json={**settings, "security": "tls", "sender": "x@dawam.test"}
    )

    assert response.status_code == 200
    assert response.json()["has_password"] is True
    admin_client.post(f"{SMTP}/test", json={"to": "ada@example.com"})
    assert outbox.last_smtp is not None
    assert outbox.last_smtp.password == "smtp password 1"


def test_a_null_password_removes_it(app, admin_client):
    save(admin_client)

    response = save(admin_client, password=None)

    assert response.json()["has_password"] is False
    assert stored_password(app) is None


def test_no_username_means_no_password(app, admin_client):
    response = save(admin_client, username=None)

    assert response.status_code == 200
    assert response.json()["username"] is None
    assert response.json()["has_password"] is False
    assert stored_password(app) is None


@pytest.mark.parametrize(
    "change", [{"host": "smtp.elsewhere.example"}, {"port": 465}, {"username": "other"}]
)
def test_moving_the_password_to_another_server_or_user_needs_it_again(admin_client, change):
    """Otherwise an admin who never knew the password could send it to a server of theirs."""
    save(admin_client)
    without_password = {k: v for k, v in SETTINGS.items() if k != "password"}

    refused = admin_client.put(SMTP, json={**without_password, **change})
    accepted = admin_client.put(SMTP, json={**SETTINGS, **change, "password": "smtp password 2"})

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "smtp_password_required"
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"host": ""},
        {"host": "smtp example.com"},
        {"port": 0},
        {"port": 65536},
        {"security": "ssl"},
        {"sender": "not an address"},
        {"sender": "dawam@example.com\r\nBcc: victim@example.com"},
    ],
)
def test_invalid_settings_are_rejected(admin_client, change):
    response = save(admin_client, **change)

    assert response.status_code == 422
    assert admin_client.get(SMTP).json() is None


def test_test_send_uses_the_saved_settings(admin_client, outbox: InMemoryOutbox):
    save(admin_client)

    response = admin_client.post(f"{SMTP}/test", json={"to": "grace@example.com"})

    assert response.status_code == 200
    assert response.json() == {"to": "grace@example.com"}
    [message] = outbox.sent_to("grace@example.com")
    assert message.subject == "DAWAM test email"
    assert outbox.last_smtp == SmtpConfig(
        host="smtp.example.com",
        port=587,
        security="starttls",
        sender="dawam@example.com",
        username="dawam",
        password="smtp password 1",
    )


def test_test_send_goes_to_the_admin_when_no_recipient_is_given(admin_client, admin_user, outbox):
    save(admin_client)

    response = admin_client.post(f"{SMTP}/test", json={})

    assert response.json() == {"to": admin_user.email}
    assert [m.subject for m in outbox.sent_to(admin_user.email)] == ["DAWAM test email"]


def test_test_send_reports_why_delivery_failed(admin_client, outbox: InMemoryOutbox):
    save(admin_client)
    outbox.fail_with("Could not reach the SMTP server smtp.example.com:587: refused")

    response = admin_client.post(f"{SMTP}/test", json={"to": "grace@example.com"})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "smtp_failed"
    assert "Could not reach the SMTP server" in error["message"]


def test_test_send_without_settings_is_a_conflict(admin_client, outbox):
    response = admin_client.post(f"{SMTP}/test", json={"to": "grace@example.com"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "smtp_not_configured"
    assert outbox.messages == []


def test_an_admin_turns_smtp_off(app, admin_client):
    save(admin_client)

    response = admin_client.delete(SMTP)

    assert response.status_code == 204
    assert admin_client.get(SMTP).json() is None
    assert stored_password(app) is None


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", SMTP, None),
        ("PUT", SMTP, SETTINGS),
        ("DELETE", SMTP, None),
        ("POST", f"{SMTP}/test", {"to": "grace@example.com"}),
    ],
)
def test_only_admins_may_manage_smtp(anonymous_client, signed_in_client, method, path, body):
    csrf = {CSRF_HEADER: csrf_token(anonymous_client)}
    anonymous = anonymous_client.request(method, path, json=body, headers=csrf)
    user = signed_in_client.request(method, path, json=body)

    assert anonymous.status_code == 401
    assert user.status_code == 403
    assert user.json()["error"]["code"] == "forbidden"
    assert signed_in_client.get("/api/v1/me").status_code == 200


def test_changing_smtp_settings_is_a_security_event(app, admin_client, admin_user, clock):
    save(admin_client)
    admin_client.delete(SMTP)

    removed, changed = SecurityEventRecorder(app.state.engine, clock=clock).recent()[:2]

    assert changed.event_type == "smtp_settings_changed"
    assert changed.actor_id == admin_user.id
    assert changed.metadata["host"] == "smtp.example.com"
    assert changed.metadata["credentials_replaced"] is True
    assert "smtp password 1" not in str(changed.metadata)
    assert removed.event_type == "smtp_settings_removed"
    assert removed.actor_id == admin_user.id
