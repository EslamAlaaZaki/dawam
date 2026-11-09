"""A chat answer or a job when the model fails and a fallback takes over (ticket #550),
through the HTTP API with the scripted fake provider behind every model."""

# ruff: noqa: F811
from __future__ import annotations

from dawam.modules.llm import LlmError, Reply
from dawam.modules.llm.gateway import AUTH
from tests.assistant.test_chat import (  # noqa: F401 (model and settings are fixtures)
    ask,
    base,
    conversation,
    done,
    model,
    settings,
    tool,
    used,
    workspace_agents,
)
from tests.assistant.test_jobs import answer_job, jobs_of, submit
from tests.llm.test_roles_budgets import BASE as LLM
from tests.llm.test_roles_budgets import register
from tests.roles import RoleClients


def with_backup(admin_client, roles: RoleClients, model, *others):
    """``backup`` is the admin's agent fallback and one of the Workspace's agent models."""
    backup = register(admin_client, "backup")
    admin_client.put(f"{LLM}/fallbacks/agent", json={"model_ids": [backup["id"]]})
    workspace_agents(roles.client("owner"), roles, model, *others, backup)
    return backup


def test_a_picked_model_that_fails_hands_over_and_the_answer_says_so(
    roles: RoleClients, model, admin_client, fake_llm
):
    other = register(admin_client, "other")
    backup = with_backup(admin_client, roles, model, other)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="from backup"))

    final = done(ask(owner, roles, cid, "hi", model_id=other["id"]))

    assert final["run"]["status"] == "completed"
    assert [c.model for c in fake_llm.calls if c.kind == "chat"] == ["other", "backup"]
    assert final["model"] == "backup" and final["model_id"] == backup["id"]
    assert final["fell_back"] is True and final["pick_moved"] is True
    detail = owner.get(f"{base(roles)}/conversations/{cid}").json()
    answer = detail["messages"][-1]
    assert answer["model"] == "backup" and answer["fell_back"] is True
    assert detail["conversation"]["model_id"] == backup["id"]  # the picker follows it

    again = done(ask(owner, roles, cid, "and now?", model_id=other["id"]))  # switched back

    assert used(fake_llm) == "other" and again["fell_back"] is False
    detail = owner.get(f"{base(roles)}/conversations/{cid}").json()
    assert detail["conversation"]["model_id"] == other["id"]
    assert detail["messages"][-1]["fell_back"] is False


def test_the_default_model_failing_hands_over_too(
    roles: RoleClients, model, admin_client, fake_llm
):
    with_backup(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="from backup"))

    final = done(ask(owner, roles, cid, "hi"))

    assert final["model"] == "backup" and final["fell_back"] is True


def test_every_model_failing_shows_each_error(roles: RoleClients, model, admin_client, fake_llm):
    with_backup(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(LlmError(AUTH, "bad key"), LlmError("bad_request", "refused it"))

    run = done(ask(owner, roles, cid, "hi"))["run"]

    assert run["status"] == "failed"
    assert "assistant-model: bad key" in run["error_message"]
    assert "backup: refused it" in run["error_message"]


def test_a_job_follows_the_same_rules_and_its_log_names_the_model(
    roles: RoleClients, model, admin_client, fake_llm
):
    with_backup(admin_client, roles, model)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Look", task="Look around"),)),
        LlmError(AUTH, "bad key"),  # the job's first step
        Reply(text="Nothing to change."),  # the backup answers the job
        Reply(text="Started."),  # the chat's own answer
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    ask(owner, roles, cid, "Look")

    [job] = jobs_of(roles)
    assert job["status"] == "succeeded"
    assert "backup took over from assistant-model, which failed: bad key" in job["log"]
    messages = owner.get(f"{base(roles)}/conversations/{cid}").json()["messages"]
    [report] = [m["content"] for m in messages if m["content"].startswith("Job finished")]
    assert "backup took over from assistant-model" in report


def test_a_light_model_falling_back_leaves_the_pick_alone(
    roles: RoleClients, model, admin_client, fake_llm
):
    light = register(admin_client, "light-model", roles=("light",))
    spare = register(admin_client, "spare-light", roles=("light",))
    admin_client.put(f"{LLM}/fallbacks/light", json={"model_ids": [spare["id"]]})
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={
            "internal_only": False,
            "share_profiles": False,
            "share_documents": False,
            "share_samples": False,
            "agent_model_ids": [model["id"]],
            "light_model_ids": [light["id"], spare["id"]],
            "default_light_model_id": light["id"],
        },
    )
    assert response.status_code == 200, response.text
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="short answer"))

    final = done(ask(owner, roles, cid, "What is a KPI?"))

    assert [c.model for c in fake_llm.calls if c.kind == "chat"][:2] == [
        "light-model",
        "spare-light",
    ]
    assert final["model"] == "spare-light" and final["fell_back"] is True
    assert final["pick_moved"] is False
    detail = owner.get(f"{base(roles)}/conversations/{cid}").json()
    assert detail["conversation"]["model_id"] is None
    assert detail["messages"][-1]["fell_back"] is True


def test_a_resumed_job_stays_on_the_model_that_took_over(
    app, services, roles: RoleClients, model, admin_client, fake_llm
):
    with_backup(admin_client, roles, model)
    fake_llm.calls.clear()
    fake_llm.script(
        LlmError(AUTH, "bad key for ada@example.com"),
        Reply(tool_calls=(tool("ask_member", "c1", question="Which tables?"),)),
        Reply(text="Done."),  # after the answer
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    job = submit(app, services, roles, cid=cid)
    assert jobs_of(roles)[0]["status"] == "paused"
    asked = len(fake_llm.calls)

    assert answer_job(roles, job.id, "The customer ones.").status_code == 200

    [resumed] = jobs_of(roles)
    assert resumed["status"] == "succeeded"
    assert [c.model for c in fake_llm.calls[asked:] if c.kind == "chat"] == ["backup"]
    assert "ada@example.com" not in resumed["log"]  # the provider's error, redacted
    assert "backup took over from assistant-model" in resumed["log"]
