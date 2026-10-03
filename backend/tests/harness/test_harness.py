"""The S1 harness itself: emails are captured and background work runs inline."""

from fastapi import FastAPI

from dawam.app import Services
from dawam.modules.jobs import InlineJobRunner
from dawam.platform.email import EmailMessage, InMemoryOutbox


def test_app_sends_email_through_the_test_outbox(app: FastAPI, outbox: InMemoryOutbox):
    services: Services = app.state.services

    services.email.send(EmailMessage(to="ada@example.com", subject="Welcome", body="Hi Ada"))

    assert outbox.messages == [EmailMessage(to="ada@example.com", subject="Welcome", body="Hi Ada")]
    assert [m.subject for m in outbox.sent_to("ada@example.com")] == ["Welcome"]
    assert outbox.sent_to("bob@example.com") == []


def test_background_jobs_run_inline_before_submit_returns(
    app: FastAPI, jobs: InlineJobRunner, outbox: InMemoryOutbox
):
    services: Services = app.state.services
    assert services.jobs is jobs

    def send_reminder(payload):
        services.email.send(EmailMessage(to=payload["to"], subject="Reminder", body=""))

    jobs.register("send_reminder", send_reminder)
    jobs.submit("send_reminder", {"to": "ada@example.com"})

    assert [m.subject for m in outbox.sent_to("ada@example.com")] == ["Reminder"]


def test_each_test_starts_with_an_empty_outbox(outbox: InMemoryOutbox):
    assert outbox.messages == []
