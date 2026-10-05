"""Background jobs (spec story 46): the Postgres queue, status, progress, log, cancel."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from dawam import job_handlers
from dawam.app import Services, create_app
from dawam.modules.jobs import (
    InlineJobRunner,
    JobService,
    QueuedJobRunner,
    UnknownJobTypeError,
)
from dawam.worker import run_pending_jobs
from tests.roles import RoleClients


def handle_ok(params, ctx):
    ctx.log(f"hello {params['name']} from job {ctx.job_id}")
    ctx.progress(50)
    ctx.log("halfway")


def handle_boom(params, ctx):
    ctx.log("about to fail")
    raise RuntimeError("the source refused the connection")


@pytest.fixture
def queued(app, clock) -> JobService:
    runner = QueuedJobRunner()
    runner.register("ok", handle_ok)
    runner.register("boom", handle_boom)
    return JobService(app.state.engine, runner=runner, clock=clock)


@pytest.fixture
def inline(app, clock) -> JobService:
    runner = InlineJobRunner()
    runner.register("ok", handle_ok)
    runner.register("boom", handle_boom)
    return JobService(app.state.engine, runner=runner, clock=clock)


def submit(service: JobService, roles: RoleClients, job_type="ok", *, as_role="owner"):
    roles.client(as_role)
    return service.submit(
        roles.workspace_id,
        job_type,
        {"name": "world"},
        title=f"{job_type} job",
        created_by=roles.user(as_role).id,
    )


def get(roles: RoleClients, job_id, as_role="owner"):
    return roles.client(as_role).get(f"/api/v1/jobs/{job_id}")


def cancel(roles: RoleClients, job_id, as_role="owner"):
    return roles.client(as_role).post(f"/api/v1/jobs/{job_id}/cancel")


def unread(roles: RoleClients, as_role="owner"):
    return roles.client(as_role).get("/api/v1/notifications").json()["items"]


def test_a_submitted_job_waits_in_the_queue(queued, roles):
    job = submit(queued, roles)

    assert (job.status, job.progress, job.log) == ("queued", 0, "")
    body = get(roles, job.id).json()
    assert body["status"] == "queued"
    assert body["type"] == "ok"


def test_an_unknown_type_is_refused(queued, roles):
    with pytest.raises(UnknownJobTypeError):
        submit(queued, roles, job_type="nope")


def test_inline_jobs_run_before_submit_returns(inline, roles):
    job = submit(inline, roles)

    assert (job.status, job.progress) == ("succeeded", 100)
    assert f"hello world from job {job.id}" in job.log
    assert "halfway" in job.log
    assert job.started_at is not None and job.finished_at is not None


def test_a_succeeded_job_notifies_its_creator(inline, roles):
    job = submit(inline, roles, as_role="editor")

    [note] = unread(roles, "editor")
    assert (note["kind"], note["ref_type"], note["ref_id"]) == ("job", "job", str(job.id))
    assert unread(roles, "owner") == []


def test_a_failed_job_keeps_its_log_and_says_why(inline, roles):
    job = submit(inline, roles, job_type="boom")

    assert job.status == "failed"
    assert job.error == "the source refused the connection"
    assert "about to fail" in job.log
    [note] = unread(roles)
    assert "failed" in note["message"]
    assert "refused the connection" in note["message"]


def test_the_worker_claims_and_runs_queued_jobs_oldest_first(queued, roles, clock, settings):
    first = submit(queued, roles)
    clock.advance(timedelta(seconds=1))
    second = submit(queued, roles, job_type="boom")

    ran = run_pending_jobs(queued, settings, stop=threading.Event())

    assert ran == 2
    assert get(roles, first.id).json()["status"] == "succeeded"
    assert get(roles, second.id).json()["status"] == "failed"
    assert queued.claim_next() is None


def test_two_claims_never_take_the_same_job(queued, roles):
    submit(queued, roles)

    first, second = queued.claim_next(), queued.claim_next()

    assert first is not None and first.status == "running"
    assert second is None


def test_a_job_whose_worker_died_is_failed_with_a_reason(queued, roles, clock):
    job = submit(queued, roles)
    assert queued.claim_next() is not None
    clock.advance(timedelta(seconds=30))
    assert queued.fail_lost() == 0

    clock.advance(timedelta(seconds=60))
    assert queued.fail_lost() == 1

    body = get(roles, job.id).json()
    assert body["status"] == "failed"
    assert "worker" in body["error"]
    assert len(unread(roles)) == 1


def test_a_heartbeat_keeps_a_running_job_alive(queued, roles, clock):
    job = submit(queued, roles)
    queued.claim_next()
    clock.advance(timedelta(seconds=50))
    queued.heartbeat(job.id)
    clock.advance(timedelta(seconds=50))

    assert queued.fail_lost() == 0


def test_a_job_submitted_in_a_callers_transaction_is_queued_only_if_it_commits(queued, app, roles):
    with Session(app.state.engine) as db, db.begin():
        kept = submit_in(queued, db, roles)
    with pytest.raises(RuntimeError), Session(app.state.engine) as db, db.begin():
        dropped = submit_in(queued, db, roles)
        raise RuntimeError("the caller's change failed")

    assert get(roles, kept.id).json()["status"] == "queued"
    assert get(roles, dropped.id).status_code == 404


def test_an_inline_job_in_a_callers_transaction_runs_once_it_commits(inline, app, roles):
    with Session(app.state.engine) as db, db.begin():
        job = submit_in(inline, db, roles)
        assert job.status == "queued"

    assert get(roles, job.id).json()["status"] == "succeeded"


def submit_in(service: JobService, db: Session, roles: RoleClients):
    return service.submit(
        roles.workspace_id, "ok", {"name": "db"}, title="ok job", created_by=None, db=db
    )


def test_a_worker_whose_heartbeat_fails_still_waits_for_its_job(app, roles, clock, settings):
    finished = threading.Event()

    def slow(params, ctx):
        time.sleep(0.3)
        finished.set()

    class HeartbeatFails(JobService):
        def heartbeat(self, job_id):
            raise OperationalError("UPDATE jobs", {}, Exception("connection lost"))

    runner = QueuedJobRunner()
    runner.register("slow", slow)
    service = HeartbeatFails(app.state.engine, runner=runner, clock=clock)
    job = submit(service, roles, job_type="slow")
    fast = settings.model_copy(update={"worker_heartbeat_seconds": 0.05})

    assert run_pending_jobs(service, fast, stop=threading.Event()) == 1
    assert finished.is_set()
    assert get(roles, job.id).json()["status"] == "succeeded"


def test_the_app_and_the_worker_share_one_registry_of_handlers(
    monkeypatch, settings, outbox, clock
):
    monkeypatch.setattr(job_handlers, "JOB_HANDLERS", {"ping": lambda params, ctx: None})
    runner = QueuedJobRunner()

    create_app(settings, services=Services(email=outbox, jobs=runner, clock=clock))

    assert runner.handler("ping") is job_handlers.JOB_HANDLERS["ping"]


# Reading


def test_any_member_sees_a_job_and_others_get_404(queued, roles):
    job = submit(queued, roles)

    assert get(roles, job.id, "viewer").status_code == 200
    assert get(roles, job.id, "non_member").status_code == 404
    assert get(roles, job.id, "anonymous").status_code == 401
    assert get(roles, uuid.uuid4()).status_code == 404


def test_the_workspace_lists_its_jobs_newest_first(queued, roles, clock):
    ids = []
    for _ in range(3):
        ids.append(str(submit(queued, roles).id))
        clock.advance(timedelta(seconds=1))
    url = f"/api/v1/workspaces/{roles.workspace_id}/jobs"

    page = roles.client("viewer").get(url, params={"limit": 2}).json()
    assert [j["id"] for j in page["items"]] == [ids[2], ids[1]]
    rest = roles.client("viewer").get(url, params={"limit": 2, "cursor": page["next_cursor"]})
    assert [j["id"] for j in rest.json()["items"]] == [ids[0]]
    assert rest.json()["next_cursor"] is None
    assert roles.client("non_member").get(url).status_code == 404


# Cancelling


def test_a_member_cancels_their_own_job(queued, roles):
    job = submit(queued, roles, as_role="editor")

    response = cancel(roles, job.id, "editor")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert queued.claim_next() is None


def test_only_an_owner_cancels_someone_elses_job(queued, roles):
    job = submit(queued, roles, as_role="editor")

    assert cancel(roles, job.id, "viewer").status_code == 403
    assert cancel(roles, job.id, "non_member").status_code == 404
    assert cancel(roles, job.id, "admin").status_code == 404
    assert cancel(roles, job.id, "owner").status_code == 200


def test_a_finished_job_cannot_be_cancelled(inline, roles):
    job = submit(inline, roles)

    response = cancel(roles, job.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_finished"
    assert get(roles, job.id).json()["status"] == "succeeded"


def test_cancelling_a_running_job_stops_it_and_keeps_it_cancelled(app, roles, clock):
    started, release = threading.Event(), threading.Event()

    def handler(params, ctx):
        ctx.log("started")
        started.set()
        release.wait(10)
        ctx.raise_if_cancelled()
        ctx.log("not reached")

    runner = QueuedJobRunner()
    runner.register("wait", handler)
    service = JobService(app.state.engine, runner=runner, clock=clock)
    job = submit(service, roles, job_type="wait")
    claimed = service.claim_next()
    worker = threading.Thread(target=service.execute, args=(claimed,))
    worker.start()
    assert started.wait(10)

    assert cancel(roles, job.id).status_code == 200
    release.set()
    worker.join(10)

    body = get(roles, job.id).json()
    assert body["status"] == "cancelled"
    assert "not reached" not in body["log"]
    assert unread(roles) == []


# Archiving


def test_archiving_a_workspace_cancels_its_queued_and_running_jobs(queued, inline, roles):
    finished = submit(inline, roles)
    running = submit(queued, roles)
    assert queued.claim_next().id == running.id
    waiting = submit(queued, roles, job_type="boom")

    response = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/archive")

    assert response.status_code == 204
    for job in (running, waiting):
        body = get(roles, job.id).json()
        assert body["status"] == "cancelled"
        assert body["finished_at"] is not None
    assert get(roles, finished.id).json()["status"] == "succeeded"
    assert queued.claim_next() is None
