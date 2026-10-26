"""Conversation titles from the light model (AI enhancements, ticket 26.5).

The first answer of a new conversation is followed by a short title written by the light
model. With no light model the feature is off and the title is the first line of the
question. Every model is the scripted ``FakeAdapter``.
"""

# ruff: noqa: F811
from __future__ import annotations

from dawam.modules.assistant.service import TITLE_PROMPT
from dawam.modules.llm import Reply
from tests.assistant.test_chat import ask, base, conversation, model, settings  # noqa: F401
from tests.assistant.test_compaction import add_light, chats


def title_calls(fake_llm):
    return [c for c in chats(fake_llm, "light-model") if c.messages[0].content == TITLE_PROMPT]


def title_of(owner, roles, cid):
    return owner.get(f"{base(roles)}/conversations/{cid}").json()["conversation"]["title"]


def test_a_light_model_titles_a_new_conversation(roles, model, admin_client, fake_llm):
    add_light(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(
        Reply(text="Net Revenue is revenue minus returns."), Reply(text='"Net Revenue meaning."')
    )
    ask(owner, roles, cid, "What is Net Revenue?")
    assert len(title_calls(fake_llm)) == 1
    assert title_of(owner, roles, cid) == "Net Revenue meaning"


def test_without_a_light_model_the_title_is_the_first_question(roles, model, fake_llm):
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(Reply(text="It is revenue."))
    ask(owner, roles, cid, "What is Net Revenue?")
    assert title_of(owner, roles, cid) == "What is Net Revenue?"
    assert title_calls(fake_llm) == []


def test_a_renamed_conversation_and_later_messages_keep_their_title(
    roles, model, admin_client, fake_llm
):
    add_light(admin_client, roles, model)
    owner = roles.client("owner")
    cid = conversation(owner, roles, title="Mine")
    fake_llm.script(Reply(text="Answer one."), Reply(text="Answer two."))
    ask(owner, roles, cid, "First?")
    ask(owner, roles, cid, "Second?")
    assert title_of(owner, roles, cid) == "Mine"
    assert title_calls(fake_llm) == []
