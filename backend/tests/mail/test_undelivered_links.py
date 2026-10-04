"""Without SMTP (or when it fails), links DAWAM would email are kept for an admin to copy
to the recipient (spec §6.1, "Without SMTP"). Every module sends through the mail
module's delivery service."""

import re
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from dawam.modules.mail import MailService
from dawam.platform.clock import FakeClock
from dawam.platform.csrf import CSRF_HEADER
from dawam.platform.email import EmailMessage, InMemoryOutbox, OneTimeLink
from tests.helpers import csrf_token

LINKS = "/api/v1/admin/undelivered-links"
TOKEN = re.compile(r"#token=([A-Za-z0-9_-]+)$")


@pytest.fixture
def mail(app, anonymous_client, outbox, clock) -> MailService:
    return MailService(app.state.engine, app.state.settings, sender=outbox, clock=clock)


@pytest.fixture
def grace(create_user):
    return create_user(email="grace@example.com")


def forgot(client, email: str):
    return client.post(
        "/api/v1/auth/password/forgot",
        json={"email": email},
        headers={CSRF_HEADER: csrf_token(client)},
    )


def links(admin_client) -> list[dict]:
    response = admin_client.get(LINKS)
    assert response.status_code == 200
    return response.json()["items"]


def test_without_smtp_a_reset_link_is_kept_for_admins(
    anonymous_client, admin_client, grace, outbox, clock
):
    response = forgot(anonymous_client, grace.email)

    assert response.status_code == 202
    assert outbox.messages == []
    [link] = links(admin_client)
    assert link["recipient"] == "grace@example.com"
    assert link["subject"] == "Reset your DAWAM password"
    assert link["purpose"] == "password_reset"
    assert link["reason"] == "smtp_not_configured"
    assert link["url"].startswith("http://localhost:8000/reset-password#token=")
    assert link["expires_at"] == (clock() + timedelta(minutes=30)).isoformat().replace(
        "+00:00", "Z"
    )


def test_the_copied_link_resets_the_password(anonymous_client, admin_client, grace):
    forgot(anonymous_client, grace.email)
    [link] = links(admin_client)
    token = TOKEN.search(link["url"])[1]

    response = anonymous_client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "password": "a brand new password"},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 204


def test_a_reset_removes_the_users_now_dead_reset_links(
    anonymous_client, admin_client, grace, create_user, clock
):
    create_user(email="ada@example.com")
    forgot(anonymous_client, grace.email)
    clock.advance(timedelta(minutes=5))  # past the per-account cooldown
    forgot(anonymous_client, grace.email)
    forgot(anonymous_client, "ada@example.com")
    token = TOKEN.search(links(admin_client)[-1]["url"])[1]  # grace's first link

    anonymous_client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "password": "a brand new password"},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert [link["recipient"] for link in links(admin_client)] == ["ada@example.com"]


def test_sending_purges_expired_links(app, mail, clock: FakeClock):
    """Storage-property check: reads the mail module's own table."""
    message = EmailMessage(to="ada@example.com", subject="Join", body="...")
    soon = OneTimeLink(url="http://x/1", purpose="invitation", expires_at=clock() + timedelta(1))
    mail.send(message, link=soon)
    clock.advance(timedelta(days=2))

    mail.send(
        message,
        link=OneTimeLink(url="http://x/2", purpose="invitation", expires_at=clock() + timedelta(1)),
    )

    with app.state.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM undelivered_links")) == 1


def test_withdrawing_links_removes_only_that_recipients_purpose(mail, admin_client, clock):
    expires = clock() + timedelta(days=1)
    for to, purpose in [
        ("a@x.example", "password_reset"),
        ("a@x.example", "invitation"),
        ("b@x.example", "password_reset"),
    ]:
        mail.send(
            EmailMessage(to=to, subject=purpose, body="..."),
            link=OneTimeLink(url=f"http://x/{to}/{purpose}", purpose=purpose, expires_at=expires),
        )

    mail.withdraw_links(recipient="a@x.example", purpose="password_reset")

    assert sorted((link["recipient"], link["purpose"]) for link in links(admin_client)) == [
        ("a@x.example", "invitation"),
        ("b@x.example", "password_reset"),
    ]


def test_an_unknown_email_leaves_no_link(anonymous_client, admin_client):
    forgot(anonymous_client, "nobody@example.com")

    assert links(admin_client) == []


def test_when_smtp_fails_the_link_is_kept_too(
    anonymous_client, admin_client, grace, mail, outbox: InMemoryOutbox
):
    mail.save_smtp_settings(
        host="smtp.example.com", port=25, security="none", sender="d@example.com", username=None
    )
    outbox.fail_with("Could not reach the SMTP server smtp.example.com:25")

    response = forgot(anonymous_client, grace.email)

    assert response.status_code == 202
    [link] = links(admin_client)
    assert link["reason"] == "smtp_failed"


def test_with_smtp_the_link_is_emailed_and_not_kept(
    anonymous_client, admin_client, grace, mail, outbox
):
    mail.save_smtp_settings(
        host="smtp.example.com", port=25, security="none", sender="d@example.com", username=None
    )

    forgot(anonymous_client, grace.email)

    assert len(outbox.sent_to(grace.email)) == 1
    assert links(admin_client) == []


def test_expired_links_are_no_longer_shown(anonymous_client, admin_client, grace, clock: FakeClock):
    forgot(anonymous_client, grace.email)

    clock.advance(timedelta(minutes=30))

    assert links(admin_client) == []


def test_links_are_listed_newest_first(admin_client, mail, clock: FakeClock):
    for name in ("first", "second"):
        mail.send(
            EmailMessage(to=f"{name}@example.com", subject=name, body="..."),
            link=OneTimeLink(
                url=f"http://x/{name}", purpose="invitation", expires_at=clock() + timedelta(days=7)
            ),
        )
        clock.advance(timedelta(minutes=1))

    assert [link["recipient"] for link in links(admin_client)] == [
        "second@example.com",
        "first@example.com",
    ]


def test_an_admin_dismisses_a_link(anonymous_client, admin_client, grace):
    forgot(anonymous_client, grace.email)
    [link] = links(admin_client)

    response = admin_client.delete(f"{LINKS}/{link['id']}")
    again = admin_client.delete(f"{LINKS}/{link['id']}")

    assert response.status_code == 204
    assert links(admin_client) == []
    assert again.status_code == 404


def test_kept_links_are_stored_encrypted(app, anonymous_client, admin_client, grace):
    """Storage-property check: reads the mail module's own table."""
    forgot(anonymous_client, grace.email)
    [link] = links(admin_client)
    token = TOKEN.search(link["url"])[1]

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM undelivered_links")).mappings().all()

    assert len(rows) == 1
    assert all(token not in str(value) for value in rows[0].values())


def test_send_reports_what_became_of_a_message(mail, outbox, clock):
    message = EmailMessage(to="ada@example.com", subject="Hello", body="Hi")
    link = OneTimeLink(url="http://x/l", purpose="invitation", expires_at=clock() + timedelta(1))

    assert mail.send(message) == "not_sent"
    assert mail.send(message, link=link) == "link_for_admin"
    mail.save_smtp_settings(
        host="smtp.example.com", port=25, security="none", sender="d@example.com", username=None
    )
    assert mail.send(message, link=link) == "sent"
    assert outbox.messages == [message]


@pytest.mark.parametrize(
    ("method", "path"), [("GET", LINKS), ("DELETE", f"{LINKS}/{uuid.uuid4()}")]
)
def test_only_admins_see_or_dismiss_links(anonymous_client, signed_in_client, method, path):
    csrf = {CSRF_HEADER: csrf_token(anonymous_client)}

    assert anonymous_client.request(method, path, headers=csrf).status_code == 401
    user = signed_in_client.request(method, path)
    assert user.status_code == 403
    assert user.json()["error"]["code"] == "forbidden"
