"""Optional auto-retrieve on a conversation's first message (AI RAG, ticket 23.6).

With the Workspace's switch on, the first message carries the top 3 passages for it, quoted
as data; off, nothing changes. The model is the scripted ``FakeAdapter``.
"""

# ruff: noqa: F811
from __future__ import annotations

from dawam.modules.llm import Reply
from tests.assistant import test_chat
from tests.assistant.test_chat import ask, conversation, done
from tests.assistant.test_source_tools import level, source, upload  # noqa: F401
from tests.roles import RoleClients

model = test_chat.model
settings = test_chat.settings


def auto_retrieve(roles: RoleClients, on: bool):
    return roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={"internal_only": False, "data_sharing_level": "documents", "auto_retrieve": on},
    )


def seen_by_the_model(fake_llm) -> str:
    return "\n".join(m.content or "" for m in fake_llm.calls[0].messages)


def test_the_setting_is_off_by_default_and_owners_switch_it(roles, model):
    path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
    assert roles.client("owner").get(path).json()["auto_retrieve"] is False

    assert auto_retrieve(roles, True).json()["auto_retrieve"] is True
    assert roles.client("viewer").get(path).json()["auto_retrieve"] is True
    plain = roles.client("owner").put(
        path, json={"internal_only": False, "data_sharing_level": "documents"}
    )
    assert plain.json()["auto_retrieve"] is True  # left out: kept


def test_the_first_message_carries_the_top_passages_quoted(roles, model, fake_llm, source):
    for i in range(5):
        upload(roles, source, f"run{i}.md", f"# Settlement {i}\nSettlement runs nightly {i}.")
    auto_retrieve(roles, True)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(Reply(text="ok"))

    assert done(ask(owner, roles, cid, "when do settlement runs happen?"))["run"]["status"] == (
        "completed"
    )

    [call] = [c for c in fake_llm.calls if c.kind == "chat"]
    last = call.messages[-1].content
    assert last.startswith("when do settlement runs happen?")
    assert '<data source="auto-retrieved:documents">' in last
    assert "Settlement runs nightly" in last
    assert last.count('"file_id"') == 3
    assert not any(m.role == "tool" for m in call.messages)


def test_only_the_first_message_carries_passages(roles, model, fake_llm, source):
    upload(roles, source, "design.md", "# Settlement\nSettlement runs nightly.")
    auto_retrieve(roles, True)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(Reply(text="ok"), Reply(text="again"))
    ask(owner, roles, cid, "settlement runs")
    fake_llm.calls.clear()

    ask(owner, roles, cid, "settlement runs again")

    assert "auto-retrieved" not in seen_by_the_model(fake_llm)
    assert fake_llm.calls[0].messages[-1].content == "settlement runs again"


def test_when_disabled_the_first_message_is_unchanged(roles, model, fake_llm, source):
    upload(roles, source, "design.md", "# Settlement\nSettlement runs nightly.")
    auto_retrieve(roles, False)
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(Reply(text="ok"))

    ask(owner, roles, cid, "settlement runs")

    assert fake_llm.calls[0].messages[-1].content == "settlement runs"
    assert "auto-retrieved" not in seen_by_the_model(fake_llm)


def test_below_the_documents_level_nothing_is_added(roles, model, fake_llm, source):
    upload(roles, source, "design.md", "# Settlement\nSettlement runs nightly.")
    auto_retrieve(roles, True)
    level(roles, "metadata")  # keeps the switch (left out), lowers what the AI may see
    owner = roles.client("owner")
    cid = conversation(owner, roles)
    fake_llm.script(Reply(text="ok"))

    ask(owner, roles, cid, "settlement runs")

    assert fake_llm.calls[0].messages[-1].content == "settlement runs"
