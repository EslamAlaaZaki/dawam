"""A job run by a supervisor that routes to specialists (ADR 0009), behind a flag.

The supervisor plans and hands parts of the task to a specialist (``delegate``); each
specialist is the same agent loop with fewer tools and a context of its own (only its task).
Their tool calls count against the job's one cap, run as the member, and a specialist whose
worker died resumes from its own checkpoint. Through the gateway with the scripted
``FakeAdapter``; jobs run inline, so the fake model is asked in the order the runs happen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from dawam.modules.jobs import JobService, QueuedJobRunner
from dawam.modules.llm import Reply
from dawam.platform.config import Settings
from tests.assistant.test_chat import add_kpi, ask, conversation, tool, tool_names
from tests.assistant.test_jobs import (
    JOB_CAP,
    JOB_TYPE,
    WorkerKilled,
    _DyingAdapter,
    answer_job,
    change_sets,
    jobs_of,
    propose,
    schema_ids,
    submit,
)
from tests.assistant.test_jobs import model as model

SUPERVISOR_TOOLS = {"delegate", "write_plan", "update_plan", "ask_member"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "assistant_max_tool_calls": 3,
            "assistant_job_max_tool_calls": JOB_CAP,
            "assistant_job_supervisor": True,
        }
    )


def delegate(specialist: str, task: str, call_id: str = "d1"):
    return tool("delegate", call_id, specialist=specialist, task=task)


def test_the_flag_is_off_by_default():
    assert Settings.model_fields["assistant_job_supervisor"].default is False


def test_the_supervisor_plans_and_routes_and_the_specialist_has_fewer_tools_and_its_own_context(
    app, services, roles, model, fake_llm
):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("write_plan", "p1", steps=["Read the KPIs", "Summarise"]),)),
        Reply(tool_calls=(delegate("kpi_analyst", "Read KPI " + kpi),)),
        Reply(tool_calls=(tool("get_object", "k1", kind="kpi", id=kpi),)),  # the specialist
        Reply(text="Net Revenue is described."),  # the specialist's report
        Reply(text="All KPIs are described."),  # the supervisor's summary
    )

    job = submit(app, services, roles, task="Describe the KPIs")

    assert job.status == "succeeded", job.log
    supervisor = tool_names(fake_llm, 0)
    assert supervisor >= SUPERVISOR_TOOLS
    assert not supervisor & {"get_object", "propose_changes", "generate_dw_schema"}
    specialist = tool_names(fake_llm, 2)
    assert "get_object" in specialist and "suggest_kpis" in specialist
    assert not specialist & {"delegate", "ask_member", "write_plan", "generate_dw_schema"}
    sent = fake_llm.calls[2].messages  # its own context: the supervisor's turns are not in it
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[1].content == "Read KPI " + kpi
    assert "kpi analyst" in (sent[0].content or "").lower()
    back = fake_llm.calls[4].messages[-1]  # the specialist's report is the delegate result
    assert back.role == "tool" and "Net Revenue is described." in (back.content or "")
    assert "<data" in (back.content or "")
    assert [s["text"] for s in jobs_of(roles)[0]["plan"]] == ["Read the KPIs", "Summarise"]
    assert "get_object ok" in job.log and "delegate ok" in job.log


def test_a_specialist_cannot_call_a_tool_outside_its_set(app, services, roles, model, fake_llm):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(delegate("kpi_analyst", "Describe a table"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),  # not a KPI analyst's tool
        Reply(text="I could not."),
        Reply(text="Nothing was proposed."),
    )

    job = submit(app, services, roles)

    assert job.status == "succeeded"
    assert "propose_changes error" in job.log
    assert change_sets(roles) == []


def test_specialist_proposals_make_the_jobs_one_change_set_and_are_verified(
    app, services, roles, model, fake_llm
):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(delegate("source_analyst", "Describe core.customers"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Described it."),
        Reply(text="The table is described."),
    )

    job = submit(app, services, roles)

    assert job.status == "succeeded", job.log
    [created] = change_sets(roles)
    assert created["item_counts"] == {"pending": 1}
    assert "validate_proposals ok" in job.log  # the supervisor's run checks the whole


def test_specialist_calls_count_against_the_jobs_one_cap(app, services, roles, model, fake_llm):
    kpi = add_kpi(roles)
    reads = tuple(tool("get_object", f"k{n}", kind="kpi", id=kpi) for n in range(JOB_CAP))
    fake_llm.script(
        Reply(tool_calls=(delegate("kpi_analyst", "Read everything"),)),
        Reply(tool_calls=reads),
        Reply(text="Read what I could."),
        Reply(tool_calls=(delegate("kpi_analyst", "Read more", "d2"),)),
        Reply(text="Done."),
    )

    job = submit(app, services, roles)

    assert job.status == "succeeded", job.log
    # The cap is 6: the first delegate is one, its specialist may make the other five.
    assert job.log.count("get_object ok") == JOB_CAP - 1
    assert job.log.count("get_object skipped") == 1
    assert "delegate skipped" in job.log  # nothing is left for a second specialist


def test_a_specialist_keeps_the_members_authorisation(roles, model, fake_llm):
    table, system = schema_ids(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Describe", task="Describe tables"),)),
        Reply(tool_calls=(delegate("source_analyst", "Describe core.customers"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Refused."),
        Reply(text="Refused."),
        Reply(text="Started."),
    )
    viewer = roles.client("viewer")

    ask(viewer, roles, conversation(viewer, roles), "Describe")

    assert change_sets(roles) == []
    assert "propose_changes refused" in jobs_of(roles, "viewer")[0]["log"]


def test_a_specialist_whose_worker_died_resumes_without_repeating_its_finished_calls(
    app, services, roles, model
):
    table, system = schema_ids(roles)
    kpi = add_kpi(roles)
    adapter = _DyingAdapter(at=4).script(
        Reply(tool_calls=(delegate("source_analyst", "Describe core.customers"),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(tool_calls=(tool("get_object", "c2", kind="kpi", id=kpi),)),
        Reply(tool_calls=(propose(table, system, "b", "y"),)),  # after the restart
        Reply(text="Described both."),
        Reply(text="The table is described."),
    )
    services.llm_adapters = lambda kind, config: adapter
    runner = QueuedJobRunner()
    runner.register(JOB_TYPE, services.jobs.handler(JOB_TYPE))
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    job = submit(app, services, roles, queue=queue)
    with pytest.raises(WorkerKilled):
        queue.execute(queue.claim_next())

    services.clock.advance(timedelta(minutes=5))
    assert queue.fail_lost() == 1
    queue.execute(queue.claim_next())

    [resumed] = jobs_of(roles)
    assert resumed["id"] == str(job.id) and resumed["status"] == "succeeded", resumed["log"]
    assert resumed["log"].count("propose_changes ok") == 2
    assert resumed["log"].count("get_object ok") == 1  # not run again
    sent = adapter.calls[3].messages  # the specialist's first model call after the restart
    assert [m.role for m in sent] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    [created] = change_sets(roles)
    assert created["item_counts"] == {"pending": 2}
    # Six model calls of 10 + 5 tokens, three before the restart and three after.
    assert "60 prompt and 30 completion tokens" in resumed["log"]


def test_a_resumed_job_keeps_finished_specialists_tokens_and_the_mode_it_began_in(
    app, services, roles, model
):
    kpi = add_kpi(roles)
    adapter = _DyingAdapter(at=4).script(
        Reply(tool_calls=(delegate("kpi_analyst", "Read KPI " + kpi),)),
        Reply(tool_calls=(tool("get_object", "k1", kind="kpi", id=kpi),)),
        Reply(text="Read it."),  # the specialist has finished when the worker dies
        Reply(text="Done."),
    )
    services.llm_adapters = lambda kind, config: adapter
    handler = services.jobs.handler(JOB_TYPE)
    runner = QueuedJobRunner()
    runner.register(JOB_TYPE, handler)
    queue = JobService(app.state.engine, runner=runner, clock=services.clock)
    submit(app, services, roles, queue=queue)
    with pytest.raises(WorkerKilled):
        queue.execute(queue.claim_next())

    handler.__self__._job_supervisor = False  # the flag is turned off meanwhile
    services.clock.advance(timedelta(minutes=5))
    assert queue.fail_lost() == 1
    queue.execute(queue.claim_next())

    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded", resumed["log"]
    assert "delegate" in tool_names(adapter, 3)  # still the supervisor
    assert resumed["log"].count("get_object ok") == 1
    assert "40 prompt and 20 completion tokens" in resumed["log"]


def test_the_supervisor_asks_the_member_and_resumes_with_the_answer(
    app, services, roles, model, fake_llm
):
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("ask_member", "q1", question="Which KPIs matter?"),)),
        Reply(tool_calls=(delegate("kpi_analyst", "Read KPI " + kpi),)),  # after the answer
        Reply(tool_calls=(tool("get_object", "k1", kind="kpi", id=kpi),)),
        Reply(text="Read it."),
        Reply(text="Done."),
    )
    job = submit(app, services, roles)
    [paused] = jobs_of(roles)
    assert paused["status"] == "paused" and paused["question"] == "Which KPIs matter?"

    assert answer_job(roles, job.id, "Revenue.").status_code == 200

    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded", resumed["log"]
    assert "get_object ok" in resumed["log"]


def test_a_specialists_expensive_call_pauses_the_job_and_resumes_the_specialist(
    app, services, roles, model, fake_llm
):
    fake_llm.script(
        Reply(tool_calls=(delegate("source_analyst", "Index the documents again"),)),
        Reply(tool_calls=(tool("reindex_documents", "r1"),)),  # the specialist
        Reply(text="Re-indexed."),  # the specialist's report, after the yes
        Reply(text="Done."),
    )
    job = submit(app, services, roles)
    [paused] = jobs_of(roles)
    assert paused["status"] == "paused" and "re-index every document" in paused["question"]
    assert "reindex_documents" not in paused["log"]

    assert answer_job(roles, job.id, "Yes").status_code == 200

    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded", resumed["log"]
    assert "reindex_documents ok" in resumed["log"]
