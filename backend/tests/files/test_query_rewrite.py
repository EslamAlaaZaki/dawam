"""Query rewriting (#377): with a light model, a search also runs the model's variants of the
query (including its Arabic <-> English translation) and fuses the rankings. Without a light
model the feature is off. The light model is the scripted fake provider."""

from __future__ import annotations

import pytest

from dawam.modules.llm import LlmError, Reply
from tests.files import test_indexing
from tests.files.test_chunk_context import light_calls, use_light
from tests.files.test_files import system, upload
from tests.files.test_indexing import search
from tests.files.test_search import Policy
from tests.roles import RoleClients

worker = test_indexing.worker

DOC = b"# Loans\n\nA loan is marked D when it is in default."
ARABIC = "ما هو القرض المتعثر"


@pytest.fixture(autouse=True)
def policy(services, fake_llm) -> Policy:
    policy = Policy(includes_documents=True)
    services.document_ai = policy
    return policy


def test_an_arabic_question_finds_an_english_passage_through_its_variant(
    roles: RoleClients, admin_client, fake_llm, worker
):
    use_light(roles, admin_client)
    system_id = system(roles)
    fake_llm.script(Reply(text="header"))
    upload(roles, system_id, "loans.md", DOC)
    worker()

    fake_llm.script(Reply(text="What is a defaulted loan\nANSWER: A loan is in default."))
    hits = search(roles, ARABIC)

    assert [p["document"] for p in hits] == ["loans.md"]
    call = light_calls(fake_llm)[-1]
    assert f'<data source="query">\n{ARABIC}\n</data>' in call.messages[1].content


def test_without_a_light_model_the_query_is_searched_as_typed(roles: RoleClients, fake_llm, worker):
    system_id = system(roles)
    upload(roles, system_id, "loans.md", DOC)
    worker()

    assert search(roles, ARABIC) == []
    assert [p["document"] for p in search(roles, "default")] == ["loans.md"]
    assert light_calls(fake_llm) == []


def test_a_failing_light_model_searches_as_typed(
    roles: RoleClients, admin_client, fake_llm, worker
):
    use_light(roles, admin_client)
    system_id = system(roles)
    fake_llm.script(Reply(text="header"))
    upload(roles, system_id, "loans.md", DOC)
    worker()

    fake_llm.script(LlmError("bad_request", "nope"))
    assert [p["document"] for p in search(roles, "default")] == ["loans.md"]
