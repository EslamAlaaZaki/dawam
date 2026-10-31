"""Long assistant tasks as background jobs (spec §6.16, story 153).

The chat hands a request to a job (``start_job``); the job runs the same agent loop as the
member with a higher tool-call cap, reports progress and ends in at most one Change Set.
Budget exhaustion, a provider failure or cancellation abort it: no Change Set, log kept.
Jobs run inline in tests, so the fake model is asked for the chat's turn, then the job's,
then the chat's answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from dawam.modules.jobs import JobService, QueuedJobRunner
from dawam.modules.llm import FakeAdapter, LlmError, Reply
from dawam.platform.config import Settings
from tests.assistant.test_chat import (
    add_kpi,
    ask,
    base,
    conversation,
    done,
    tool,
)
from tests.assistant.test_source_tools import level
from tests.llm.test_roles_budgets import BASE as LLM
from tests.llm.test_roles_budgets import register, use_models
from tests.roles import RoleClients

JOB_CAP = 6
JOB_TYPE = "assistant_task"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"assistant_max_tool_calls": 3, "assistant_job_max_tool_calls": JOB_CAP}
    )


@pytest.fixture
def model(admin_client, fake_llm, roles: RoleClients):
    assert roles.workspace_id  # created first, while there is no model to choose
    agent = register(admin_client, "assistant-model")
    assert use_models(roles.client("owner"), roles.workspace_id, agent).status_code == 200
    fake_llm.calls.clear()  # the connection test is not what the tests look at
    return agent


def jobs_of(roles: RoleClients, role: str = "owner") -> list[dict[str, Any]]:
    response = roles.client(role).get(f"/api/v1/workspaces/{roles.workspace_id}/jobs")
    assert response.status_code == 200, response.text
    return [job for job in response.json()["items"] if job["type"] == JOB_TYPE]


def change_sets(roles: RoleClients) -> list[dict[str, Any]]:
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/change-sets")
    return response.json()["items"]


def job_service(app, services) -> JobService:
    return JobService(app.state.engine, runner=services.jobs, clock=services.clock)


def submit(
    app, services, roles: RoleClients, task: str = "Explore the schema", cid=None, queue=None
):
    return (queue or job_service(app, services)).submit(
        roles.workspace_id,
        JOB_TYPE,
        {
            "user_id": str(roles.user("owner").id),
            "workspace_id": str(roles.workspace_id),
            "title": "Explore",
            "conversation_id": cid,
            "task": task,
        },
        title="Explore",
        created_by=roles.user("owner").id,
    )


def propose(table: str, system: str, key: str, text: str = "x"):
    return tool(
        "propose_changes",
        key,
        title=f"Describe {key}",
        source_system_id=system,
        items=[
            {
                "key": key,
                "object_type": "source_table",
                "object_id": table,
                "changes": {"description": text},
                "label": "core.customers",
            }
        ],
    )


def schema_ids(roles: RoleClients) -> tuple[str, str]:
    from tests.authz.matrix import table_id

    table = table_id(roles)
    systems = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/systems")
    return table, systems.json()["items"][0]["id"]


# -- handing a request to a job ------------------------------------------------------------


def test_the_chat_hands_a_request_to_a_job_and_shows_its_progress(roles, model, fake_llm):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Explore KPIs", task="Read every KPI"),)),
        Reply(tool_calls=(tool("get_object", "j1", kind="kpi", id=kpi),)),  # the job
        Reply(text="Every KPI is described."),  # the job's answer
        Reply(text="I started a job for it."),  # the chat's answer
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    events = ask(owner, roles, cid, "Explore everything")

    [(_, started)] = [e for e in events if e[0] == "tool" and e[1]["name"] == "start_job"]
    assert started["status"] == "ok"
    [job] = jobs_of(roles)
    assert started["result"]["job_id"] == job["id"]
    assert job["status"] == "succeeded" and job["progress"] == 100
    assert "get_object ok" in job["log"]
    assert done(events)["run"]["status"] == "completed"
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    contents = [m["content"] for m in messages]
    assert "I started a job for it." in contents
    assert "Job finished: Every KPI is described." in contents  # the job reports into the chat


def test_the_job_is_started_as_the_member_with_its_own_task_as_the_only_message(
    roles, model, fake_llm
):
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="T", task="Do the long thing"),)),
        Reply(text="done"),
        Reply(text="ok"),
    )
    owner = roles.client("owner")

    ask(owner, roles, conversation(owner, roles), "Go")

    job_call = fake_llm.calls[1]
    assert [m.role for m in job_call.messages] == ["system", "user"]
    assert job_call.messages[1].content == "Do the long thing"
    assert "start_job" not in {t.name for t in job_call.tools}  # a job does not start jobs
    assert jobs_of(roles)[0]["created_by"] == str(roles.user("owner").id)


def test_a_job_has_a_higher_tool_call_cap_than_the_chat(app, services, roles, model, fake_llm):
    kpi = add_kpi(roles)
    calls = tuple(tool("get_object", f"c{n}", kind="kpi", id=kpi) for n in range(JOB_CAP + 1))
    fake_llm.script(Reply(tool_calls=calls), Reply(text="Read what I could."))

    job = submit(app, services, roles)

    assert job.status == "succeeded"
    assert job.log.count("get_object ok") == JOB_CAP  # the chat's cap is 3
    assert job.log.count("get_object skipped") == 1


# -- one Change Set ------------------------------------------------------------------------


def test_a_job_ends_in_one_change_set_however_many_proposals_it_made(roles, model, fake_llm):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Describe", task="Describe tables"),)),
        Reply(tool_calls=(propose(table, system, "a"), propose(table, system, "b"))),
        Reply(text="Proposed."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")

    events = ask(owner, roles, conversation(owner, roles), "Describe")

    assert [d["status"] for n, d in events if n == "tool"] == ["ok"]
    [created] = change_sets(roles)
    assert created["status"] == "pending" and created["origin"] == "ai"
    assert created["item_counts"] == {"pending": 2}
    assert created["title"] == "Describe"
    assert jobs_of(roles)[0]["status"] == "succeeded"


def test_a_job_without_proposals_creates_no_change_set(roles, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Look", task="Look around"),)),
        Reply(text="Nothing to change."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")

    ask(owner, roles, conversation(owner, roles), "Look")

    assert change_sets(roles) == []
    assert jobs_of(roles)[0]["status"] == "succeeded"


# -- abort paths ---------------------------------------------------------------------------


def test_a_provider_failure_aborts_the_job_with_no_change_set_and_keeps_the_log(
    roles, model, fake_llm
):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Describe", task="Describe tables"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        LlmError("auth", "The provider refused the API key."),
        Reply(text="The job failed."),
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    ask(owner, roles, cid, "Describe")

    [job] = jobs_of(roles)
    assert job["status"] == "failed"
    assert job["error"] == "The provider refused the API key."
    assert "propose_changes ok" in job["log"]  # the log is kept
    assert change_sets(roles) == []
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    [report] = [m["content"] for m in messages if m["content"].startswith("Job failed")]
    assert "The provider refused the API key." in report
    assert "No changes were proposed" in report


def test_a_spent_budget_aborts_the_job(app, services, roles, model, fake_llm, admin_client):
    budget = admin_client.put(
        f"{LLM}/budgets/workspaces/{roles.workspace_id}", json={"monthly_token_budget": 0}
    )
    assert budget.status_code in (200, 204), budget.text

    job = submit(app, services, roles)

    assert job.status == "failed" and "budget" in (job.error or "")
    assert fake_llm.calls == [] and change_sets(roles) == []


def test_no_assigned_model_aborts_the_job(app, services, roles, fake_llm):
    job = submit(app, services, roles)

    assert job.status == "failed" and "no agent model" in (job.error or "")


class _CancellingAdapter(FakeAdapter):
    """Cancels the job when the model is asked its n-th time."""

    def __init__(self, press: Any, at: int) -> None:
        super().__init__()
        self._press, self._at = press, at

    def chat(self, model, messages, tools, stream, json_schema) -> Iterator[Any]:
        if sum(1 for c in self.calls if c.kind == "chat") + 1 == self._at:
            self._press()
        yield from super().chat(model, messages, tools, stream, json_schema)


def test_cancelling_a_job_aborts_it_with_no_change_set(app, services, roles, model):
    table, system = schema_ids(roles)
    owner = roles.user("owner")
    service = job_service(app, services)

    def press() -> None:
        [running] = [j for j in service.list(owner, roles.workspace_id).items if j.type == JOB_TYPE]
        service.cancel(owner, running.id)

    adapter = _CancellingAdapter(press, at=2).script(
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="this answer is never used"),
    )
    services.llm_adapters = lambda kind, config: adapter

    submit(app, services, roles)

    [job] = jobs_of(roles)
    assert job["status"] == "cancelled"
    assert "propose_changes ok" in job["log"]
    assert change_sets(roles) == []


def test_a_job_started_by_a_viewer_cannot_propose_changes(roles, model, fake_llm):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Describe", task="Describe tables"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Refused."),
        Reply(text="Started."),
    )
    viewer = roles.client("viewer")

    ask(viewer, roles, conversation(viewer, roles), "Describe")

    assert change_sets(roles) == []
    assert "propose_changes refused" in jobs_of(roles, "viewer")[0]["log"]


def test_a_bad_proposal_is_reported_to_the_model_and_the_rest_still_make_the_change_set(
    roles, model, fake_llm
):
    import uuid

    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Describe", task="Describe tables"),)),
        Reply(tool_calls=(propose(str(uuid.uuid4()), system, "bad"),)),
        Reply(tool_calls=(propose(table, system, "good"),)),
        Reply(text="Proposed."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")

    ask(owner, roles, conversation(owner, roles), "Describe")

    [created] = change_sets(roles)
    assert created["item_counts"] == {"pending": 1}
    assert jobs_of(roles)[0]["status"] == "succeeded"
    assert (
        "propose_changes error" in jobs_of(roles)[0]["log"] or "refused" in jobs_of(roles)[0]["log"]
    )


def test_a_job_aborts_when_its_requester_is_no_longer_a_member(
    app, services, roles, model, fake_llm
):
    from sqlalchemy import text

    viewer = roles.user("viewer")
    with app.state.engine.begin() as db:
        db.execute(
            text("delete from workspace_members where user_id = :u and workspace_id = :w"),
            {"u": viewer.id, "w": roles.workspace_id},
        )

    job = job_service(app, services).submit(
        roles.workspace_id,
        JOB_TYPE,
        {
            "user_id": str(viewer.id),
            "workspace_id": str(roles.workspace_id),
            "title": "Explore",
            "conversation_id": None,
            "task": "Explore",
        },
        title="Explore",
        created_by=viewer.id,
    )

    assert job.status == "failed" and fake_llm.calls == []
    assert change_sets(roles) == []


# -- resuming after the worker died -------------------------------------------------------


class WorkerKilled(BaseException):
    """The worker process dies: nothing in the job sees it coming or records it."""


class _DyingAdapter(FakeAdapter):
    """The worker dies when the model is asked its n-th time."""

    def __init__(self, at: int) -> None:
        super().__init__(context_window=32_768)
        self._at = at

    def chat(self, model, messages, tools, stream, json_schema) -> Iterator[Any]:
        if sum(1 for c in self.calls if c.kind == "chat") + 1 == self._at:
            self._at = 0
            raise WorkerKilled
        yield from super().chat(model, messages, tools, stream, json_schema)


def test_a_job_whose_worker_died_resumes_without_repeating_finished_tool_calls(
    app, services, roles, model
):
    table, system = schema_ids(roles)
    kpi = add_kpi(roles)
    adapter = _DyingAdapter(at=3).script(
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(tool_calls=(tool("get_object", "c2", kind="kpi", id=kpi),)),
        Reply(tool_calls=(propose(table, system, "b", "y"),)),  # after the restart
        Reply(text="Described both."),
    )
    services.llm_adapters = lambda kind, config: adapter
    runner = QueuedJobRunner()  # through the worker's queue, not inline
    runner.register(JOB_TYPE, services.jobs.handler(JOB_TYPE))
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    job = submit(app, services, roles, queue=queue)
    with pytest.raises(WorkerKilled):
        queue.execute(queue.claim_next())

    services.clock.advance(timedelta(minutes=5))  # the worker restarts
    assert queue.fail_lost() == 1
    queue.execute(queue.claim_next())

    [resumed] = jobs_of(roles)
    assert resumed["id"] == str(job.id) and resumed["status"] == "succeeded"
    assert resumed["log"].count("propose_changes ok") == 2
    assert resumed["log"].count("get_object ok") == 1  # not run again
    sent = adapter.calls[-2].messages  # the first model call after the restart
    assert [m.role for m in sent] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    [created] = change_sets(roles)
    assert created["item_counts"] == {"pending": 2}  # the proposal made before it died too


def test_a_job_checkpoint_is_stored_redacted(app, services, roles, model):
    import json

    import sqlalchemy as sa

    kpi = add_kpi(roles, name="Ask jane.doe@example.com")
    adapter = _DyingAdapter(at=2).script(
        Reply(tool_calls=(tool("get_object", "c1", kind="kpi", id=kpi),)),
    )
    services.llm_adapters = lambda kind, config: adapter
    runner = QueuedJobRunner()
    runner.register(JOB_TYPE, services.jobs.handler(JOB_TYPE))
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    job = submit(app, services, roles, queue=queue)
    with pytest.raises(WorkerKilled):
        queue.execute(queue.claim_next())

    with app.state.engine.connect() as db:
        query = sa.text("select checkpoint from jobs where id = :id")
        saved = json.dumps(db.execute(query, {"id": job.id}).scalar())
    assert "get_object" in saved
    assert "jane.doe@example.com" not in saved and "redacted" in saved


# -- asking the member ---------------------------------------------------------------------


def answer_job(roles: RoleClients, job_id, answer="Use the KPIs.", as_role="owner"):
    return roles.client(as_role).post(f"/api/v1/jobs/{job_id}/answer", json={"answer": answer})


def test_a_job_pauses_to_ask_the_member_and_resumes_with_the_answer(
    app, services, roles, model, fake_llm
):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("write_plan", "p1", steps=["Read", "Ask", "Finish"]),)),
        Reply(tool_calls=(tool("get_object", "c1", kind="kpi", id=kpi),)),
        Reply(tool_calls=(tool("ask_member", "c2", question="Which KPIs matter?"),)),
        Reply(tool_calls=(tool("get_object", "c3", kind="kpi", id=kpi),)),  # after the answer
        Reply(text="Described them."),
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    job = submit(app, services, roles, cid=cid)

    [paused] = jobs_of(roles)
    assert paused["id"] == str(job.id) and paused["status"] == "paused"
    assert paused["question"] == "Which KPIs matter?"
    assert [s["text"] for s in paused["plan"]] == ["Read", "Ask", "Finish"]  # kept while paused
    assert "Asked the member" in paused["log"]
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    assert any("waiting for your answer: Which KPIs matter?" in m["content"] for m in messages)
    assert change_sets(roles) == []

    response = answer_job(roles, job.id, "  The revenue ones.  ")

    assert response.status_code == 200, response.text
    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded" and resumed["question"] is None
    assert [s["text"] for s in resumed["plan"]] == ["Read", "Ask", "Finish"]
    assert resumed["log"].count("get_object ok") == 2  # nothing before the question ran again
    sent = fake_llm.calls[-1].messages  # the last model call carries the answer as quoted data
    answered = [m for m in sent if m.role == "tool" and "The revenue ones." in (m.content or "")]
    assert len(answered) == 1 and "<data" in (answered[0].content or "")
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    assert "Job finished: Described them." in [m["content"] for m in messages]


def test_ask_member_is_a_barrier_never_run_inside_a_batch_of_reads(
    app, services, roles, model, fake_llm
):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(
            tool_calls=(
                tool("get_object", "c1", kind="kpi", id=kpi),
                tool("get_object", "c2", kind="kpi", id=kpi),
                tool("ask_member", "c3", question="Which one?"),
                tool("get_object", "c4", kind="kpi", id=kpi),
            )
        ),
        Reply(text="Done."),
    )
    job = submit(app, services, roles)

    [paused] = jobs_of(roles)
    assert paused["status"] == "paused"
    assert paused["log"].count("get_object ok") == 2  # the reads before it, not the one after

    assert answer_job(roles, job.id, "That one.").status_code == 200
    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded" and resumed["log"].count("get_object ok") == 3


def test_a_chat_is_not_offered_ask_member_but_a_job_is(app, services, roles, model, fake_llm):
    fake_llm.script(Reply(text="Done."))
    submit(app, services, roles)
    assert "ask_member" in {t.name for t in fake_llm.calls[0].tools}

    fake_llm.calls.clear()
    owner = roles.client("owner")
    fake_llm.script(Reply(text="Hi."))
    ask(owner, roles, conversation(owner, roles), "Hello")
    assert "ask_member" not in {t.name for t in fake_llm.calls[0].tools}


def test_only_the_member_who_started_a_paused_job_answers_it(app, services, roles, model, fake_llm):
    fake_llm.script(Reply(tool_calls=(tool("ask_member", "c1", question="Which one?"),)))
    job = submit(app, services, roles)

    assert answer_job(roles, job.id, as_role="editor").status_code == 403
    assert answer_job(roles, job.id, "  ").status_code == 422
    assert jobs_of(roles)[0]["status"] == "paused"


def test_a_job_that_is_not_waiting_cannot_be_answered(app, services, roles, model, fake_llm):
    fake_llm.script(Reply(text="Nothing to ask."))
    job = submit(app, services, roles)

    response = answer_job(roles, job.id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_not_paused"


def test_an_unanswered_job_can_be_cancelled(app, services, roles, model, fake_llm):
    fake_llm.script(Reply(tool_calls=(tool("ask_member", "c1", question="Which one?"),)))
    job = submit(app, services, roles)

    response = roles.client("owner").post(f"/api/v1/jobs/{job.id}/cancel")

    assert response.status_code == 200, response.text
    [cancelled] = jobs_of(roles)
    assert cancelled["status"] == "cancelled" and cancelled["question"] is None
    assert answer_job(roles, job.id).status_code == 409
    assert change_sets(roles) == []


def test_a_paused_job_can_be_answered_from_the_worker_queue(app, services, roles, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("ask_member", "c1", question="Which one?"),)),
        Reply(text="Used your answer."),
    )
    runner = QueuedJobRunner()
    runner.register(JOB_TYPE, services.jobs.handler(JOB_TYPE))
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    job = submit(app, services, roles, queue=queue)
    queue.execute(queue.claim_next())
    owner = roles.user("owner")
    assert queue.get(owner, job.id).status == "paused"
    assert queue.claim_next() is None  # a paused job is not picked up

    queue.answer(owner, job.id, "The first.")

    assert queue.get(owner, job.id).status == "queued"
    queue.execute(queue.claim_next())
    assert queue.get(owner, job.id).status == "succeeded"


# -- planning (#403) -------------------------------------------------------------------------


def test_a_job_shows_its_plan_and_keeps_it_current_as_it_runs(roles, model, fake_llm):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Explore", task="Read every KPI"),)),
        Reply(tool_calls=(tool("write_plan", "p1", steps=["Read KPIs", "Summarise"]),)),
        Reply(
            tool_calls=(
                tool("update_plan", "p2", updates=[{"id": 1, "status": "failed"}]),
                tool("get_object", "p3", kind="kpi", id=kpi),
            )
        ),
        Reply(
            tool_calls=(
                tool(
                    "update_plan",
                    "p4",
                    updates=[{"id": 2, "status": "done"}],
                    add=["Retry reading"],
                ),
            )
        ),
        Reply(text="Done."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")
    ask(owner, roles, conversation(owner, roles), "Explore everything")

    [job] = jobs_of(roles)
    assert job["status"] == "succeeded"
    assert job["plan"] == [
        {"id": 1, "text": "Read KPIs", "status": "failed"},
        {"id": 2, "text": "Summarise", "status": "done"},
        {"id": 3, "text": "Retry reading", "status": "pending"},
    ]
    chat_tools = {t.name for t in fake_llm.calls[0].tools}
    job_tools = {t.name for t in fake_llm.calls[1].tools}
    assert not chat_tools & {"write_plan", "update_plan"}  # only a job plans
    assert {"write_plan", "update_plan"} <= job_tools


def test_with_tool_search_on_a_job_still_gets_the_plan_tools_up_front(
    roles, model, fake_llm, monkeypatch
):
    from dawam.modules.assistant.internal import agent

    monkeypatch.setattr(agent, "TOOL_SEARCH_THRESHOLD", 5)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Explore", task="Read every KPI"),)),
        Reply(text="Done."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")
    ask(owner, roles, conversation(owner, roles), "Explore everything")

    chat_tools = {t.name for t in fake_llm.calls[0].tools}
    job_tools = {t.name for t in fake_llm.calls[1].tools}
    assert "find_tools" in chat_tools and "find_tools" in job_tools  # tool search is on
    assert not chat_tools & {"write_plan", "update_plan"}
    assert {"write_plan", "update_plan"} <= job_tools
    finder = next(t for t in fake_llm.calls[1].tools if t.name == "find_tools")
    assert "write_plan" not in finder.description


def test_a_bad_plan_update_is_reported_to_the_model(roles, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Explore", task="Plan"),)),
        Reply(tool_calls=(tool("update_plan", "p1", updates=[{"id": 1, "status": "done"}]),)),
        Reply(text="Done."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")
    ask(owner, roles, conversation(owner, roles), "Plan it")

    [job] = jobs_of(roles)
    assert "update_plan error" in job["log"] and job["plan"] == []


def test_a_resumed_job_keeps_the_plan_it_wrote(app, services, roles, model):
    adapter = _DyingAdapter(at=3).script(
        Reply(tool_calls=(tool("write_plan", "p1", steps=["One", "Two"]),)),
        Reply(tool_calls=(tool("update_plan", "p2", updates=[{"id": 1, "status": "done"}]),)),
        Reply(tool_calls=(tool("update_plan", "p3", updates=[{"id": 2, "status": "done"}]),)),
        Reply(text="Done."),
    )
    services.llm_adapters = lambda kind, config: adapter
    runner = QueuedJobRunner()
    runner.register(JOB_TYPE, services.jobs.handler(JOB_TYPE))
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    submit(app, services, roles, queue=queue)
    with pytest.raises(WorkerKilled):
        queue.execute(queue.claim_next())
    [running] = jobs_of(roles)
    assert [s["status"] for s in running["plan"]] == ["done", "pending"]  # visible mid-run

    services.clock.advance(timedelta(minutes=5))
    assert queue.fail_lost() == 1
    queue.execute(queue.claim_next())

    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded"
    assert [s["status"] for s in resumed["plan"]] == ["done", "done"]


# -- self-verification ---------------------------------------------------------------------


def test_a_job_that_proposed_changes_checks_its_work_and_reports_the_result(
    app, services, roles, model, fake_llm
):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Described the table."),
    )

    owner = roles.client("owner")
    cid = conversation(owner, roles)
    job = submit(app, services, roles, cid=cid)

    assert job.status == "succeeded"
    assert job.log.count("run_validation") == 1 and job.log.count("get_score") == 1
    assert job.log.index("propose_changes") < job.log.index("validate_proposals")
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    [report] = [m["content"] for m in messages if m["content"].startswith("Job finished")]
    assert "Verification: Proposed changes: 0 errors. Current warehouse: validation" in report


def test_a_job_that_wrote_nothing_runs_no_checks(app, services, roles, model, fake_llm):
    fake_llm.script(Reply(text="Nothing to do."))

    job = submit(app, services, roles)

    assert "run_validation" not in job.log and "get_score" not in job.log


def test_a_proposal_the_real_validator_rejects_is_corrected_and_the_summary_reports_zero_errors(
    app, services, roles, model, fake_llm
):
    import uuid

    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(propose(str(uuid.uuid4()), system, "a"),)),  # names no table
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Described the table."),
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    job = submit(app, services, roles, cid=cid)

    assert job.status == "succeeded"
    assert job.log.index("propose_changes error") < job.log.index("propose_changes ok")
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    [report] = [m["content"] for m in messages if m["content"].startswith("Job finished")]
    assert "Proposed changes: 0 errors." in report
    assert change_sets(roles)[0]["item_counts"] == {"pending": 1}


def test_a_blank_plan_step_is_refused(roles, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Explore", task="Plan"),)),
        Reply(tool_calls=(tool("write_plan", "p1", steps=["Read", "   "]),)),
        Reply(text="Done."),
        Reply(text="Started."),
    )
    owner = roles.client("owner")
    ask(owner, roles, conversation(owner, roles), "Plan it")

    [job] = jobs_of(roles)
    assert "write_plan error" in job["log"] and job["plan"] == []


# -- approval before expensive actions -----------------------------------------------------


def test_reindexing_the_workspace_pauses_for_approval_and_runs_on_a_yes(
    app, services, roles, model, fake_llm
):
    fake_llm.script(
        Reply(tool_calls=(tool("reindex_documents", "c1"),)),
        Reply(text="Re-indexed."),
    )
    job = submit(app, services, roles)

    [paused] = jobs_of(roles)
    assert paused["status"] == "paused" and "re-index every document" in paused["question"]
    assert "reindex_documents" not in paused["log"]  # nothing ran yet

    assert answer_job(roles, job.id, "Yes").status_code == 200
    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded" and "reindex_documents ok" in resumed["log"]


def test_a_no_leaves_the_action_unrun_and_tells_the_model(app, services, roles, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("reindex_documents", "c1"),)),
        Reply(text="Skipped it."),
    )
    job = submit(app, services, roles)

    assert answer_job(roles, job.id, "No, not now").status_code == 200
    [done] = jobs_of(roles)
    assert done["status"] == "succeeded" and "reindex_documents refused" in done["log"]
    sent = fake_llm.calls[-1].messages
    assert any("did not approve" in (m.content or "") for m in sent if m.role == "tool")


def test_a_source_query_without_a_limit_or_above_the_cap_pauses(
    app, services, roles, model, fake_llm
):
    level(roles, "samples")  # the tool is offered at this data-sharing level
    sid = str(uuid.uuid4())
    fake_llm.script(
        Reply(tool_calls=(tool("run_source_query", "c1", system_id=sid, sql="SELECT * FROM a.b"),)),
        Reply(text="Done."),
    )
    job = submit(app, services, roles)
    [paused] = jobs_of(roles)
    assert paused["status"] == "paused" and "no LIMIT" in paused["question"]
    assert answer_job(roles, job.id, "no").status_code == 200


def test_a_source_query_within_the_cap_and_other_actions_do_not_pause(
    app, services, roles, model, fake_llm
):
    kpi = add_kpi(roles)
    sid = str(uuid.uuid4())
    fake_llm.script(
        Reply(
            tool_calls=(
                tool("get_object", "c1", kind="kpi", id=kpi),
                tool("run_source_query", "c2", system_id=sid, sql="SELECT a FROM s.t LIMIT 100"),
            )
        ),
        Reply(text="Done."),
    )
    submit(app, services, roles)
    [done] = jobs_of(roles)
    assert done["status"] == "succeeded" and "get_object ok" in done["log"]


@pytest.mark.parametrize(
    ("sql", "engine", "gated"),
    [
        ("SELECT * FROM a.b", "postgresql", True),
        ("SELECT * FROM a.b LIMIT 101", "postgresql", True),
        ("SELECT * FROM a.b LIMIT 100", "postgresql", False),
        ("SELECT * FROM a.b LIMIT 5", None, False),
        ("SELECT * FROM a.b LIMIT n", "postgresql", True),
        ("SELECT TOP 500 [a] FROM [t]", "sqlserver", True),
        ("SELECT TOP 5 [a] FROM [t]", "sqlserver", False),
        ("SELECT `a` FROM t", "mysql", True),
        ("SELECT `a` FROM t LIMIT 10", "mysql", False),
        ("SELECT a FROM t FETCH FIRST 5 ROWS ONLY", "oracle", False),
        ("SELECT a FROM t FETCH FIRST 500 ROWS ONLY", "oracle", True),
        ("not sql at all (", "postgresql", True),
        ("SELECT TOP 5 [a] FROM [t]", "postgresql", True),  # the wrong dialect: fails closed
    ],
)
def test_the_source_query_rule_uses_the_engine_dialect_and_fails_closed(sql, engine, gated):
    from dawam.modules.sources.internal.query_guard import is_row_bounded

    assert is_row_bounded(sql, engine=engine, cap=100) is (not gated)


@pytest.mark.parametrize("answer", ["yes", " Yes. ", "OK", "go ahead", "Approve!"])
def test_only_an_exact_yes_approves(answer):
    from dawam.modules.assistant.internal.agent import is_approval

    assert is_approval(answer)


@pytest.mark.parametrize(
    "answer", ["yes but don't touch X", "y, no", "no", "yes please skip the big one", "", "maybe"]
)
def test_a_yes_with_a_condition_is_not_approval(answer):
    from dawam.modules.assistant.internal.agent import is_approval

    assert not is_approval(answer)
