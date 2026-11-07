"""The reasoning effort a member picks per conversation (#556): it reaches the adapter, is kept
on the conversation, applies to a fallback and a background job, and is fixed at ``low`` for the
light model. Driven through the HTTP API with the scripted fake provider behind every model."""

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
    workspace_agents,
)
from tests.assistant.test_chat_fallback import with_backup
from tests.assistant.test_jobs import answer_job, jobs_of
from tests.llm.test_roles_budgets import BASE as LLM
from tests.llm.test_roles_budgets import register
from tests.roles import RoleClients


def reason(admin_client, model: dict) -> None:
    """The admin ticks "This is a reasoning model" (no effort: members choose it)."""
    response = admin_client.put(
        f"{LLM}/models/{model['id']}",
        json={"reasoning": {"enabled": True, "effort": "high", "budget_tokens": 2048}},
    )
    assert response.status_code == 200, response.text


def efforts(fake_llm) -> list[str | None]:
    return [c.reasoning.effort if c.reasoning else None for c in fake_llm.calls if c.kind == "chat"]


def stored(owner, roles, cid) -> str | None:
    detail = owner.get(f"{base(roles)}/conversations/{cid}").json()
    return detail["conversation"]["effort"]


def test_the_picked_effort_reaches_the_adapter_and_is_kept_on_the_conversation(
    roles: RoleClients, model, admin_client, fake_llm
):
    reason(admin_client, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    assert stored(owner, roles, cid) is None  # a new conversation: medium
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "hi", effort="high"))

    assert set(efforts(fake_llm)) == {"high"}
    assert stored(owner, roles, cid) == "high"  # a reload shows High
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "and again"))  # no pick: the conversation's own

    assert set(efforts(fake_llm)) == {"high"}
    done(ask(owner, roles, cid, "lighter", effort="low"))
    assert efforts(fake_llm)[-1] == "low" and stored(owner, roles, cid) == "low"


def test_a_conversation_without_a_pick_starts_on_medium(
    roles: RoleClients, model, admin_client, fake_llm
):
    reason(admin_client, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "hi"))

    assert set(efforts(fake_llm)) == {"medium"}  # the admin's stored effort is not read


def test_a_model_that_does_not_reason_ignores_the_effort(roles: RoleClients, model, fake_llm):
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "hi", effort="high"))

    assert set(efforts(fake_llm)) == {None}


def test_an_unknown_effort_is_refused(roles: RoleClients, model):
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    response = owner.post(
        f"{base(roles)}/conversations/{cid}/messages", json={"content": "hi", "effort": "max"}
    )

    assert response.status_code == 422


def test_the_workspace_agent_models_say_whether_they_reason(
    roles: RoleClients, model, admin_client
):
    plain = register(admin_client, "plain")
    reason(admin_client, model)
    owner = roles.client("owner")
    workspace_agents(owner, roles, model, plain)

    settings_ = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/ai-settings").json()

    assert {m["name"]: m["reasoning"] for m in settings_["agent_models"]} == {
        "assistant-model": True,
        "plain": False,
    }


def test_a_fallback_that_takes_over_uses_the_same_effort(
    roles: RoleClients, model, admin_client, fake_llm
):
    backup = with_backup(admin_client, roles, model)
    reason(admin_client, model)
    reason(admin_client, backup)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="from backup"))

    final = done(ask(owner, roles, cid, "hi", effort="high"))

    assert final["fell_back"] is True
    assert set(efforts(fake_llm)) == {"high"}


def test_the_light_model_always_thinks_at_low(roles: RoleClients, model, admin_client, fake_llm):
    light = register(admin_client, "light-model", roles=("light",))
    reason(admin_client, light)
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={
            "internal_only": False,
            "data_sharing_level": "metadata",
            "agent_model_ids": [model["id"]],
            "light_model_ids": [light["id"]],
        },
    )
    assert response.status_code == 200, response.text
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "What is a KPI?", effort="high"))

    chats = [c for c in fake_llm.calls if c.kind == "chat"]
    assert chats[0].model == "light-model"
    assert chats[0].reasoning is not None and chats[0].reasoning.effort == "low"


def test_a_job_uses_the_effort_the_conversation_had_when_it_started_also_after_a_pause(
    roles: RoleClients, model, admin_client, fake_llm
):
    reason(admin_client, model)
    fake_llm.script(
        Reply(tool_calls=(tool("start_job", title="Look", task="Look around"),)),
        Reply(tool_calls=(tool("ask_member", "c1", question="Which tables?"),)),  # the job
        Reply(text="Started."),  # the chat's answer
        Reply(text="Fine."),  # the second message
        Reply(text="Done."),  # the job, after the answer
    )
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()

    done(ask(owner, roles, cid, "Look", effort="high"))
    [job] = jobs_of(roles)
    assert job["status"] == "paused"
    done(ask(owner, roles, cid, "never mind", effort="low"))  # the conversation moves on
    fake_llm.calls.clear()

    assert answer_job(roles, job["id"]).status_code in (200, 202)

    assert efforts(fake_llm)[0] == "high"  # the resumed job keeps its own
