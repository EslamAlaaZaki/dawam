"""Contextual header per chunk (#369): when the Workspace has a light model, each passage gets a
one-sentence context from it, indexed (and embedded) with the passage. Without a light model the
feature is off and indexing still succeeds. Driven through the jobs queue; the light model is the
scripted fake provider."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from dawam.modules.llm import LlmError, Reply
from tests.files import test_indexing
from tests.files.test_files import system, upload
from tests.files.test_indexing import listed, search
from tests.files.test_search import Policy
from tests.llm.test_roles_budgets import use_models
from tests.llm.test_workspace_ai import register
from tests.roles import RoleClients

worker = test_indexing.worker

DOC = b"# Status codes\n\nA loan is marked D when it is in default."
HEADER = "From the Loans data dictionary, section Status codes: lists loan statuses."


@pytest.fixture(autouse=True)
def policy(services, fake_llm) -> Policy:
    policy = Policy(includes_documents=True)
    services.document_ai = policy
    return policy


def use_light(roles: RoleClients, admin_client, *, internal: bool = True) -> None:
    assert roles.workspace_id  # created first, while there is no model to choose
    agent = register(admin_client, "agent-m", roles=("agent",), internal=internal)
    light = register(admin_client, "light-m", roles=("light",), internal=internal)
    saved = use_models(roles.client("owner"), roles.workspace_id, agent, light=light)
    assert saved.status_code == 200, saved.text


def headers(roles: RoleClients) -> list[str | None]:
    with sa.orm.Session(roles.app.state.engine) as db:
        return list(db.scalars(sa.text("SELECT context_header FROM document_chunks")))


def light_calls(fake_llm) -> list:
    return [
        c
        for c in fake_llm.calls
        if c.kind == "chat" and c.model == "light-m" and len(c.messages) == 2
    ]


def test_a_chunk_carries_the_light_models_header_and_is_found_by_it(
    roles: RoleClients, admin_client, fake_llm, worker
):
    use_light(roles, admin_client)
    fake_llm.script(Reply(text=HEADER))
    system_id = system(roles)
    upload(roles, system_id, "loans.md", DOC)

    assert worker() == 1

    assert listed(roles, system_id)["loans.md"]["index_status"] == "indexed"
    assert headers(roles) == [HEADER]
    [call] = light_calls(fake_llm)
    assert 'source="passage"' in call.messages[1].content
    assert [p["document"] for p in search(roles, "dictionary")] == ["loans.md"]
    [hit] = search(roles, "dictionary")
    assert hit["text"] == DOC.decode().split("\n\n", 1)[1]  # the passage itself is unchanged


def test_without_a_light_model_the_feature_is_off_and_indexing_succeeds(
    roles: RoleClients, fake_llm, worker
):
    system_id = system(roles)
    upload(roles, system_id, "loans.md", DOC)

    worker()

    assert listed(roles, system_id)["loans.md"]["index_status"] == "indexed"
    assert headers(roles) == [None]
    assert light_calls(fake_llm) == []
    assert [p["document"] for p in search(roles, "default")] == ["loans.md"]


def test_a_failing_light_model_leaves_the_file_indexed_without_headers(
    roles: RoleClients, admin_client, fake_llm, worker
):
    use_light(roles, admin_client)
    fake_llm.script(LlmError("bad_request", "nope"))
    system_id = system(roles)
    upload(roles, system_id, "loans.md", DOC)

    worker()

    assert listed(roles, system_id)["loans.md"]["index_status"] == "indexed"
    assert headers(roles) == [None]


def test_documents_do_not_reach_an_external_light_model_below_the_documents_level(
    roles: RoleClients, admin_client, fake_llm, policy, worker
):
    policy.includes_documents = False
    use_light(roles, admin_client, internal=False)
    system_id = system(roles)
    upload(roles, system_id, "loans.md", DOC)

    worker()

    assert listed(roles, system_id)["loans.md"]["index_status"] == "indexed"
    assert light_calls(fake_llm) == []
    assert headers(roles) == [None]
