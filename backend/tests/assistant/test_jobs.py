"""Long assistant tasks as background jobs (spec §6.16, story 153).

The chat hands a request to a job (``start_job``); the job runs the same agent loop as the
member with a higher tool-call cap, reports progress and ends in at most one Change Set.
Budget exhaustion, a provider failure or cancellation abort it: no Change Set, log kept.
Jobs run inline in tests, so the fake model is asked for the chat's turn, then the job's,
then the chat's answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dawam.modules.jobs import JobService
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
from tests.llm.test_roles_budgets import BASE as LLM
from tests.llm.test_roles_budgets import assign, register
from tests.roles import RoleClients

JOB_CAP = 6
JOB_TYPE = "assistant_task"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"assistant_max_tool_calls": 3, "assistant_job_max_tool_calls": JOB_CAP}
    )


@pytest.fixture
def model(admin_client, fake_llm):
    agent = register(admin_client, "assistant-model")
    assert assign(admin_client, agent).status_code == 200
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


def submit(app, services, roles: RoleClients, task: str = "Explore the schema", cid=None):
    return job_service(app, services).submit(
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

    assert job.status == "failed" and "agent role" in (job.error or "")


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
