# ruff: noqa: F401, F811
"""Requests per minute per member and per Workspace (AI enhancements §9), through the
assistant's HTTP API: one message is one request, however many model calls it makes. Over a
limit the request fails with 429 ``ai_rate_limited``; the counts reset every minute."""

from __future__ import annotations

from datetime import timedelta

from dawam.modules.llm import Reply
from dawam.modules.llm.role_service import MEMBER_REQUESTS_PER_MINUTE, WORKSPACE_REQUESTS_PER_MINUTE
from tests.assistant.test_chat import (
    add_kpi,
    ask,
    conversation,
    model,
    refused,
    tool,
)
from tests.roles import RoleClients

LIMIT = "dawam.modules.llm.role_service."


def answers(fake_llm, count=1):
    fake_llm.script(*[Reply(text="ok") for _ in range(count)])


def test_the_defaults_are_20_per_member_and_100_per_workspace():
    assert (MEMBER_REQUESTS_PER_MINUTE, WORKSPACE_REQUESTS_PER_MINUTE) == (20, 100)


def test_a_member_over_the_limit_gets_a_clear_rate_limit_error(
    model,
    fake_llm,
    roles: RoleClients,
    monkeypatch,
):
    monkeypatch.setattr(LIMIT + "MEMBER_REQUESTS_PER_MINUTE", 3)
    client = roles.client("owner")
    cid = conversation(client, roles)
    answers(fake_llm, 3)
    for _ in range(3):
        ask(client, roles, cid, "hi")

    response = refused(client, roles, cid)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "ai_rate_limited"
    assert "requests per minute" in response.json()["error"]["message"]
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_a_tool_using_turn_counts_as_one_request(
    model,
    roles: RoleClients,
    fake_llm,
    monkeypatch,
):
    monkeypatch.setattr(LIMIT + "MEMBER_REQUESTS_PER_MINUTE", 2)
    client = roles.client("owner")
    cid = conversation(client, roles)
    kpi = add_kpi(roles)
    fake_llm.script(
        Reply(tool_calls=(tool("get_object", kind="kpi", id=kpi),)),
        Reply(tool_calls=(tool("get_object", "c2", kind="kpi", id=kpi),)),
        Reply(text="ok"),
        Reply(text="ok"),
    )

    ask(client, roles, cid, "hi")  # several model calls, one request
    ask(client, roles, cid, "again")
    assert refused(client, roles, cid).status_code == 429


def test_the_limit_is_per_member(model, fake_llm, roles: RoleClients, monkeypatch):
    monkeypatch.setattr(LIMIT + "MEMBER_REQUESTS_PER_MINUTE", 1)
    answers(fake_llm, 2)
    owner, other = roles.client("owner"), roles.client("editor")
    ask(owner, roles, conversation(owner, roles), "hi")

    ask(other, roles, conversation(other, roles), "hi")


def test_the_workspace_limit_applies_across_members(
    model,
    fake_llm,
    roles: RoleClients,
    monkeypatch,
):
    monkeypatch.setattr(LIMIT + "WORKSPACE_REQUESTS_PER_MINUTE", 2)
    answers(fake_llm, 2)
    owner, other = roles.client("owner"), roles.client("editor")
    ask(owner, roles, conversation(owner, roles), "hi")
    cid = conversation(other, roles)
    ask(other, roles, cid, "hi")

    response = refused(owner, roles, conversation(owner, roles))

    assert response.status_code == 429
    assert response.json()["error"]["details"]["scope"] == "workspace"


def test_limits_reset_the_next_minute(model, fake_llm, roles: RoleClients, clock, monkeypatch):
    monkeypatch.setattr(LIMIT + "MEMBER_REQUESTS_PER_MINUTE", 1)
    answers(fake_llm, 2)
    client = roles.client("owner")
    cid = conversation(client, roles)
    ask(client, roles, cid, "hi")
    assert refused(client, roles, cid).status_code == 429
    clock.advance(timedelta(seconds=61))

    ask(client, roles, cid, "hi")
