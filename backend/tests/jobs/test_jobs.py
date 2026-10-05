"""Background jobs (spec story 46): the Postgres queue, status, progress, log, cancel."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest

from dawam.modules.jobs import (
    InlineJobRunner,
    JobService,
    QueuedJobRunner,
    UnknownJobKindError,
)
from dawam.worker import run_pending_jobs
from tests.roles import RoleClients


def handle_ok(payload, ctx):
    ctx.log(f"hello {payload['name']}")
    ctx.progress(50)
    ctx.log("halfway")


def handle_boom(payload, ctx):
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


def submit(service: JobService, roles: RoleClients, kind="ok", *, as_role="owner"):
    roles.client(as_role)
    return service.submit(
        roles.workspace_id,
        kind,
        {"name": "world"},
        title=f"{kind} job",
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
    assert body["kind"] == "ok"


def test_an_unknown_kind_is_refused(queued, roles):
    with pytest.raises(UnknownJobKindError):
        submit(queued, roles, kind="nope")


def test_inline_jobs_run_before_submit_returns(inline, roles):
    job = submit(inline, roles)

    assert (job.status, job.progress) == ("succeeded", 100)
    assert "hello world" in job.log
    assert "halfway" in job.log
    assert job.started_at is not None and job.finished_at is not None


def test_a_succeeded_job_notifies_its_creator(inline, roles):
    job = submit(inline, roles, as_role="editor")

    [note] = unread(roles, "editor")
    assert (note["kind"], note["ref_type"], note["ref_id"]) == ("job", "job", str(job.id))
    assert unread(roles, "owner") == []


def test_a_failed_job_keeps_its_log_and_says_why(inline, roles):
    job = submit(inline, roles, kind="boom")

    assert job.status == "failed"
    assert job.error == "the source refused the connection"
    assert "about to fail" in job.log
    [note] = unread(roles)
    assert "failed" in note["message"]
    assert "refused the connection" in note["message"]


def test_the_worker_claims_and_runs_queued_jobs_oldest_first(queued, roles, clock, settings):
    first = submit(queued, roles)
    clock.advance(timedelta(seconds=1))
    second = submit(queued, roles, kind="boom")

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

    def handler(payload, ctx):
        ctx.log("started")
        started.set()
        release.wait(10)
        ctx.raise_if_cancelled()
        ctx.log("not reached")

    runner = QueuedJobRunner()
    runner.register("wait", handler)
    service = JobService(app.state.engine, runner=runner, clock=clock)
    job = submit(service, roles, kind="wait")
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
    waiting = submit(queued, roles, kind="boom")

    response = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/archive")

    assert response.status_code == 204
    for job in (running, waiting):
        body = get(roles, job.id).json()
        assert body["status"] == "cancelled"
        assert body["finished_at"] is not None
    assert get(roles, finished.id).json()["status"] == "succeeded"
    assert queued.claim_next() is None
