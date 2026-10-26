"""Conversation compaction through the HTTP API (AI foundations, ticket 20.6).

When turns no longer fit the agent model's window they are summarised by the light model
(one summary message, quoted as data) instead of being dropped; past tool calls come back
as short digests. With no light model the feature is off and old turns are dropped as
before. Every model is the scripted ``FakeAdapter``.
"""

# ruff: noqa: F811
from __future__ import annotations

from dawam.modules.llm import Reply, Usage
from tests.assistant.test_chat import (  # noqa: F401 (model and settings are fixtures)
    add_kpi,
    ask,
    base,
    conversation,
    done,
    model,
    settings,
    tool,
)
from tests.llm.test_roles_budgets import register, use_models

BIG = "lorem ipsum " * 660  # 7,920 characters: the longest message, about 2,000 tokens
WINDOW = 32_768  # the fake model's window


def add_light(admin_client, roles, model):
    """The Workspace gets a light model too (``model`` is its agent model)."""
    spare = register(admin_client, "light-model", roles=("light",))
    saved = use_models(roles.client("owner"), roles.workspace_id, model, light=spare)
    assert saved.status_code == 200
    return spare


def chats(fake_llm, name):
    return [c for c in fake_llm.calls if c.kind == "chat" and c.model == name]


def long_conversation(roles, fake_llm, turns=7):
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(*[Reply(text=BIG)] * turns)
    for i in range(turns):
        ask(owner, roles, cid, f"question {i} {BIG}")
    return owner, cid


def test_dropped_turns_are_summarised_by_the_light_model_and_kept(
    roles, model, admin_client, fake_llm
):
    owner, cid = long_conversation(roles, fake_llm)
    add_light(admin_client, roles, model)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="THE SUMMARY"), Reply(text="the answer"))

    assert done(ask(owner, roles, cid, "and now go on"))["run"]["status"] == "completed"

    assert len(chats(fake_llm, "light-model")) == 1
    seen = chats(fake_llm, "assistant-model")[0].messages
    assert "THE SUMMARY" in seen[0].content
    assert '<data source="conversation-summary">' in seen[0].content
    assert seen[-1].content == "and now go on"
    assert not any("question 0" in (m.content or "") for m in seen)


def test_the_summary_is_kept_and_not_rebuilt_on_the_next_message(
    roles, model, admin_client, fake_llm
):
    owner, cid = long_conversation(roles, fake_llm)
    add_light(admin_client, roles, model)
    fake_llm.script(Reply(text="THE SUMMARY"), Reply(text="the answer"))
    ask(owner, roles, cid, "and now go on")
    fake_llm.calls.clear()

    fake_llm.script(Reply(text="fine"))
    ask(owner, roles, cid, "once more")

    assert chats(fake_llm, "light-model") == []
    assert "THE SUMMARY" in chats(fake_llm, "assistant-model")[0].messages[0].content


def test_the_summary_counts_against_the_context_budget(roles, model, admin_client, fake_llm):
    owner, cid = long_conversation(roles, fake_llm)
    add_light(admin_client, roles, model)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="word " * 20_000), Reply(text="the answer"))

    ask(owner, roles, cid, "and now go on")

    system = chats(fake_llm, "assistant-model")[0].messages[0].content
    assert 0 < system.count("word ") * 5 <= WINDOW * 4 // 6 + 5


def test_without_a_light_model_compaction_is_off_and_old_turns_are_dropped(
    roles, model, fake_llm, admin_client
):
    owner, cid = long_conversation(roles, fake_llm)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="the answer"))

    assert done(ask(owner, roles, cid, "and now go on"))["run"]["status"] == "completed"

    assert [c.model for c in fake_llm.calls if c.kind == "chat"] == ["assistant-model"]
    seen = fake_llm.calls[0].messages
    assert "conversation-summary" not in seen[0].content
    assert "left out to fit" in seen[0].content


def test_a_failing_light_model_does_not_break_the_chat(roles, model, admin_client, fake_llm):
    from dawam.modules.llm import LlmError

    owner, cid = long_conversation(roles, fake_llm)
    add_light(admin_client, roles, model)
    fake_llm.calls.clear()
    fake_llm.script(LlmError("bad_request", "refused"), Reply(text="the answer"))

    assert done(ask(owner, roles, cid, "and now go on"))["run"]["status"] == "completed"
    assert "conversation-summary" not in chats(fake_llm, "assistant-model")[0].messages[0].content


def test_past_tool_calls_come_back_as_digests(roles, model, admin_client, fake_llm):
    add_light(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(
        Reply(tool_calls=(tool("get_object", kind="kpi", id=add_kpi(roles)),), usage=Usage(1, 1)),
        Reply(text='{"injection": false}'),  # the light model screens the tool result
        Reply(text="It is Net Revenue."),
    )
    ask(owner, roles, cid, "which KPIs exist")
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="None."))

    ask(owner, roles, cid, "and the systems")

    sent = chats(fake_llm, "assistant-model")[0].messages
    note = next(m.content for m in sent if m.role == "assistant")
    assert "It is Net Revenue." in note
    assert '<data source="past-tool-calls">' in note
    assert "get_object" in note and "Net Revenue" in note


def test_the_settings_list_the_light_features_as_on_or_off(roles, model, admin_client, fake_llm):
    owner = roles.client("owner")
    url = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"

    off = owner.get(url).json()["light_features"]
    assert [(f["key"], f["enabled"]) for f in off] == [
        ("conversation_compaction", False),
        ("chunk_context", False),
        ("query_rewriting", False),
    ]

    spare = register(admin_client, "light-model", roles=("light",))
    assert use_models(owner, roles.workspace_id, model, light=spare).status_code == 200
    on = owner.get(url).json()["light_features"]
    assert [(f["key"], f["enabled"]) for f in on] == [
        ("conversation_compaction", True),
        ("chunk_context", True),
        ("query_rewriting", True),
    ]
    assert base(roles)  # the assistant path is unchanged
