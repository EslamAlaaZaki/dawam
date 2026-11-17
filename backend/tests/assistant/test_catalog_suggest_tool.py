"""``suggest_catalog_entries`` (spec §6.16, #627): the assistant starts the data catalog
suggestion job instead of proposing table and column text as a Change Set. ``propose_changes``
keeps only relationship decisions (those are covered in ``test_documentation_drafts`` and
``test_jobs``). Jobs run inline, so the catalog job's own model call comes from the same
scripted fake provider, between the chat's tool call and its final answer."""

from __future__ import annotations

import pytest

from dawam.modules.jobs import JobService, QueuedJobRunner
from dawam.modules.llm import Reply
from dawam.modules.sources import CATALOG_SUGGEST_JOB
from tests.assistant.test_chat import ask, conversation, tool, tool_names
from tests.roles import RoleClients
from tests.sources import test_catalog_suggestions
from tests.sources.test_catalog_suggestions import answer, imported, path

model = test_catalog_suggestions.model


def change_sets(roles: RoleClients) -> list[dict]:
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/change-sets")
    return response.json()["items"]


def suggest(system: str, call_id: str = "c1"):
    return tool("suggest_catalog_entries", call_id, system_id=system.rsplit("/", 1)[1])


def chat(roles: RoleClients, fake_llm, role: str, *replies: Reply):
    fake_llm.script(*replies)
    client = roles.client(role)
    return ask(client, roles, conversation(client, roles), "Document every table")


def test_the_tool_starts_the_job_and_makes_no_change_set(roles, model, fake_llm):
    system = imported(roles)

    events = chat(
        roles,
        fake_llm,
        "editor",
        Reply(tool_calls=(suggest(system),)),
        answer(),  # the catalog job's own model call
        Reply(text="Started."),
    )

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    assert shown["status"] == "ok"
    job = roles.client("editor").get(path(system)).json()["job"]
    assert job["status"] == "succeeded"
    told = [c.messages[-1].content or "" for c in fake_llm.calls if c.kind == "chat"][-1]
    assert "started" in told and "Data catalog" in told
    assert change_sets(roles) == []


def test_the_tool_reports_a_run_that_is_already_going(roles, model, fake_llm):
    system = imported(roles)
    queue = QueuedJobRunner()
    queue.register(CATALOG_SUGGEST_JOB, lambda params, ctx: None)
    state = roles.app.state
    waiting = JobService(state.engine, runner=queue, clock=state.services.clock).submit(
        roles.workspace_id,
        CATALOG_SUGGEST_JOB,
        {"source_system_id": system.rsplit("/", 1)[1]},
        title="Suggest catalog entries",
        created_by=roles.user("editor").id,
    )

    events = chat(
        roles, fake_llm, "editor", Reply(tool_calls=(suggest(system),)), Reply(text="Already.")
    )

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    assert shown["status"] == "ok"  # reported, not an error
    told = [c.messages[-1].content or "" for c in fake_llm.calls if c.kind == "chat"][-1]
    assert "already_running" in told and str(waiting.id) in told
    assert roles.client("editor").get(path(system)).json()["job"]["job_id"] == str(waiting.id)
    assert change_sets(roles) == []


def test_a_viewer_is_not_offered_the_tool_and_cannot_start_the_job(roles, model, fake_llm):
    system = imported(roles)

    events = chat(
        roles, fake_llm, "viewer", Reply(tool_calls=(suggest(system),)), Reply(text="No.")
    )

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    assert shown["status"] == "refused"
    assert "suggest_catalog_entries" not in tool_names(fake_llm)
    assert roles.client("editor").get(path(system)).json()["job"] is None


@pytest.mark.parametrize("changes", [{"items": []}, {"items": [{"key": "t"}]}])
def test_propose_changes_no_longer_takes_table_and_column_items(roles, model, fake_llm, changes):
    system = imported(roles)
    call = tool(
        "propose_changes",
        title="Describe",
        source_system_id=system.rsplit("/", 1)[1],
        **changes,
    )

    events = chat(roles, fake_llm, "owner", Reply(tool_calls=(call,)), Reply(text="Done."))

    [(_, shown)] = [e for e in events if e[0] == "tool"]
    assert shown["status"] == "error"
    told = [c.messages[-1].content or "" for c in fake_llm.calls if c.kind == "chat"][-1]
    assert "items" in told and "not permitted" in told
    assert change_sets(roles) == []
