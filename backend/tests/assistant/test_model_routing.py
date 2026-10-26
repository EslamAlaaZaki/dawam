"""Chat turns routed to the light or agent model through the HTTP API (ticket #406)."""

# ruff: noqa: F811
from __future__ import annotations

from dawam.modules.llm import Reply
from tests.assistant.test_chat import (  # noqa: F401 (model and settings are fixtures)
    ask,
    conversation,
    done,
    model,
    settings,
)
from tests.assistant.test_compaction import add_light, chats


def test_a_plain_question_is_answered_by_the_light_model(roles, model, admin_client, fake_llm):
    add_light(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="short answer"))

    assert done(ask(owner, roles, cid, "What is a KPI?"))["run"]["status"] == "completed"

    assert len(chats(fake_llm, "light-model")) == 1
    assert chats(fake_llm, "assistant-model") == []


def test_a_request_to_generate_goes_to_the_agent_model(roles, model, admin_client, fake_llm):
    add_light(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="done"))

    done(ask(owner, roles, cid, "Create a staging table for orders"))

    assert len(chats(fake_llm, "assistant-model")) == 1
    assert chats(fake_llm, "light-model") == []


def test_without_a_light_model_the_agent_model_answers(roles, model, fake_llm):
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="agent answer"))

    done(ask(owner, roles, cid, "What is a KPI?"))

    assert len(chats(fake_llm, "assistant-model")) == 1
