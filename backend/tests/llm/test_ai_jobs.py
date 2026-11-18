# ruff: noqa: F811
"""The background AI explorer through the HTTP API (#631): the model calls made outside the
assistant, one row per job and one per Workspace per day of searches. Calls go through the
metered gateway with the scripted ``fake_llm``; jobs are rows of the ``jobs`` table."""

from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from dawam.modules.jobs.tables import JobRecord
from dawam.modules.llm import LlmError, Reply, ai_call_context, ai_jobs
from dawam.modules.llm.gateway import AUTH
from dawam.modules.llm.tables import UsageRecord
from tests.llm.test_model_fallback import text
from tests.llm.test_roles_budgets import BASE, role_service, setup  # noqa: F401
from tests.roles import RoleClients

JOBS = "/api/v1/admin/ai/jobs"


def a_job(app, clock, workspace_id, *, status="succeeded", created_by=None, **params) -> uuid.UUID:
    job_id = uuid.uuid4()
    with Session(app.state.engine) as db, db.begin():
        db.add(
            JobRecord(
                id=job_id,
                workspace_id=workspace_id,
                type="pii_ai",
                title="AI PII detection: crm",
                params={k: v for k, v in params.items()},
                status=status,
                progress=0,
                log="",
                created_by=created_by,
                created_at=clock(),
                started_at=clock(),
                finished_at=clock() + timedelta(seconds=90) if status != "running" else None,
            )
        )
    return job_id


def call(role_service, workspace_id, fake_llm, outcome=None, user_id=None):
    fake_llm.script(outcome or Reply(text="ok"))
    gateway = role_service.gateway_for_role("agent", workspace_id=workspace_id, user_id=user_id)
    with contextlib.suppress(LlmError):
        text(gateway)


def listed(admin, **params) -> list[dict]:
    response = admin.get(JOBS, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_only_admins_can_open_it(roles: RoleClients, setup):
    for who in ("owner", "editor", "viewer"):
        client = roles.client(who)
        assert client.get(JOBS).status_code == 403
        assert client.get(f"{JOBS}/{uuid.uuid4()}").status_code == 403
    assert roles.client("anonymous").get(JOBS).status_code in (401, 403)
    assert roles.client("admin").get(JOBS).status_code == 200


def test_one_row_per_job_and_per_workspace_day_of_searches(
    app, clock, roles: RoleClients, role_service, setup, fake_llm
):
    owner, viewer = roles.user("owner"), roles.user("viewer")
    job = a_job(app, clock, setup, created_by=owner.id, table_ids=["a", "b", "c"])
    with ai_call_context("pii_scan", job_id=job):
        call(role_service, setup, fake_llm)
        call(role_service, setup, fake_llm)
    with ai_call_context("search"):
        call(role_service, setup, fake_llm, user_id=owner.id)
        call(role_service, setup, fake_llm, user_id=viewer.id)
    clock.advance(timedelta(days=1))
    with ai_call_context("search"):
        call(role_service, setup, fake_llm, user_id=owner.id)
    with ai_call_context("assistant"):  # the run explorer's, not listed here
        call(role_service, setup, fake_llm)
    call(role_service, setup, fake_llm)  # no feature, no job: not listed

    rows = listed(roles.client("admin"))

    day1, day2 = clock().date() - timedelta(days=1), clock().date()
    assert [r["key"] for r in rows] == [
        f"search:{setup}:{day2}",
        str(job),
        f"search:{setup}:{day1}",
    ]
    by_key = {r["key"]: r for r in rows}
    pii = by_key[str(job)]
    assert pii["feature"] == "pii_scan"
    assert pii["target"] == "AI PII detection: crm · 3 tables"
    assert pii["started_by"] == owner.email
    assert (pii["calls"], pii["failed_calls"], pii["status"]) == (2, 0, "completed")
    assert pii["duration_ms"] == 90_000
    assert pii["models"] == ["big-model"]
    assert pii["workspace_name"]
    searches = by_key[f"search:{setup}:{day1}"]
    assert (searches["feature"], searches["calls"], searches["member_count"]) == ("search", 2, 2)
    assert searches["started_by"] == "2 members"
    assert searches["job_id"] is None and searches["target"] is None


def test_status_follows_the_job_and_its_failed_calls(
    app, clock, roles: RoleClients, role_service, setup, fake_llm
):
    running = a_job(app, clock, setup, status="running")
    failed = a_job(app, clock, setup, status="failed")
    partial = a_job(app, clock, setup)
    for job in (running, failed):
        with ai_call_context("relationships", job_id=job):
            call(role_service, setup, fake_llm)
    with ai_call_context("relationships", job_id=partial):
        call(role_service, setup, fake_llm)
        call(role_service, setup, fake_llm, LlmError(AUTH, "bad key"))

    rows = {r["key"]: r for r in listed(roles.client("admin"))}

    assert rows[str(running)]["status"] == "running"
    assert rows[str(failed)]["status"] == "failed"
    assert (rows[str(partial)]["status"], rows[str(partial)]["failed_calls"]) == ("partial", 1)
    assert rows[str(running)]["started_by"] == "Scheduled"


def test_filters(app, clock, roles: RoleClients, role_service, setup, fake_llm):
    job = a_job(app, clock, setup)
    with ai_call_context("catalog_suggestions", job_id=job):
        call(role_service, setup, fake_llm)
    with ai_call_context("search"):
        call(role_service, setup, fake_llm, LlmError(AUTH, "bad key"))
    admin = roles.client("admin")

    def keys(**params):
        return {r["key"] for r in listed(admin, **params)}

    search = f"search:{setup}:{clock().date()}"
    assert keys() == {str(job), search}
    assert keys(workspace_id=setup) == {str(job), search}
    assert keys(workspace_id=str(uuid.uuid4())) == set()
    assert keys(feature="catalog_suggestions") == {str(job)}
    assert keys(feature="search") == {search}
    assert keys(status="failed") == {search}
    assert keys(status="completed") == {str(job)}
    assert keys(started_from="2999-01-01T00:00:00Z") == set()
    assert keys(started_to="2999-01-01T00:00:00Z") == {str(job), search}
    assert keys(started_to=clock().isoformat()) == set()
    assert admin.get(JOBS, params={"status": "bogus"}).status_code == 422
    assert admin.get(JOBS, params={"feature": "assistant"}).status_code == 422


def test_the_detail_breaks_calls_down_and_lists_the_failed_ones(
    app, clock, roles: RoleClients, role_service, setup, fake_llm
):
    job = a_job(app, clock, setup)
    with ai_call_context("kpi_suggestions", job_id=job):
        call(role_service, setup, fake_llm)
        call(role_service, setup, fake_llm, LlmError(AUTH, "bad key"))
    admin = roles.client("admin")

    response = admin.get(f"{JOBS}/{job}")
    assert response.status_code == 200, response.text
    detail = response.json()

    assert detail["job"]["key"] == str(job)
    [model] = detail["models"]
    assert (model["role"], model["model_name"], model["calls"], model["failed_calls"]) == (
        "agent",
        "big-model",
        2,
        1,
    )
    assert model["avg_latency_ms"] is not None
    [failed] = detail["failed_calls"]
    assert (failed["error_code"], failed["model_name"]) == (AUTH, "big-model")
    assert set(failed) == {"at", "error_code", "role", "model_name"}  # no prompt or output
    assert admin.get(f"{JOBS}/{uuid.uuid4()}").status_code == 404
    assert admin.get(f"{JOBS}/search:nope:2026-01-01").status_code == 404
    assert admin.get(f"{JOBS}/not-a-key").status_code == 404


def test_cost_is_unknown_without_a_price_and_follows_it_with_one(
    app, clock, roles: RoleClients, role_service, setup, fake_llm, admin_client
):
    job = a_job(app, clock, setup)
    with ai_call_context("pii_scan", job_id=job):
        call(role_service, setup, fake_llm, Reply(text="ok"))
    admin = roles.client("admin")
    assert listed(admin)[0]["cost"] is None
    assert admin.get(f"{JOBS}/{job}").json()["models"][0]["cost"] is None

    priced = admin_client.put(
        f"{BASE}/prices",
        json={
            "model_name": "big-model",
            "currency": "USD",
            "input_per_million": "3",
            "output_per_million": "15",
            "cached_input_per_million": "0.3",
        },
    )
    assert priced.status_code == 200, priced.text

    [row] = listed(admin)
    assert row["cost"]["currency"] == "USD"
    expected = (Decimal(row["input_tokens"]) * 3 + Decimal(row["output_tokens"]) * 15) / 1_000_000
    assert Decimal(row["cost"]["amount"]) == expected


def test_a_job_is_one_row_whatever_workspaces_its_calls_name(
    app, clock, roles: RoleClients, role_service, setup, fake_llm
):
    job = a_job(app, clock, setup)
    with ai_call_context("file_indexing", job_id=job):
        call(role_service, setup, fake_llm)
    with Session(app.state.engine) as db, db.begin():  # a call made for no Workspace
        db.add(
            UsageRecord(
                id=uuid.uuid4(),
                created_at=clock() + timedelta(seconds=1),
                role="embedding",
                model_name="embedder",
                workspace_id=None,
                prompt_tokens=3,
                completion_tokens=0,
                estimated=True,
                feature="file_indexing",
                job_id=job,
            )
        )

    [row] = listed(roles.client("admin"))

    assert (row["key"], row["calls"]) == (str(job), 2)


def test_a_status_filter_looks_at_the_newest_candidates_only(
    app, clock, roles: RoleClients, role_service, setup, fake_llm, monkeypatch
):
    old = a_job(app, clock, setup)
    with ai_call_context("pii_scan", job_id=old):
        call(role_service, setup, fake_llm)
    clock.advance(timedelta(minutes=1))
    new = a_job(app, clock, setup, status="running")
    with ai_call_context("pii_scan", job_id=new):
        call(role_service, setup, fake_llm)
    admin = roles.client("admin")
    assert [r["key"] for r in listed(admin, status="completed")] == [str(old)]

    monkeypatch.setattr(ai_jobs, "STATUS_CANDIDATES", 1)

    assert listed(admin, status="completed") == []
    assert [r["key"] for r in listed(admin, status="running")] == [str(new)]
