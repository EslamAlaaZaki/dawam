"""Query rewriting (#377): with a light model, a search also runs the model's variants of the
query (including its Arabic <-> English translation) and fuses the rankings. Without a light
model the feature is off. The light model is the scripted fake provider."""

from __future__ import annotations

import pytest

from dawam.modules.files.internal.query_rewrite import parse_variants
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


def test_an_exhausted_token_budget_searches_as_typed(
    roles: RoleClients, admin_client, fake_llm, worker
):
    use_light(roles, admin_client)
    system_id = system(roles)
    fake_llm.script(Reply(text="header"))
    upload(roles, system_id, "loans.md", DOC)
    worker()
    budget = roles.client("admin").put(
        "/api/v1/admin/llm/budgets/installation", json={"monthly_token_budget": 0}
    )
    assert budget.status_code == 200, budget.text
    fake_llm.calls.clear()

    assert [p["document"] for p in search(roles, "default")] == ["loans.md"]
    assert light_calls(fake_llm) == []


def test_parsing_keeps_the_hyde_line_beyond_three_rewordings():
    text = "one a\ntwo b\nthree c\nfour d\nANSWER: five e"
    assert parse_variants(text, "q") == ["one a", "two b", "three c", "five e"]


def test_parsing_drops_the_query_and_duplicates_ignoring_case_and_spacing():
    text = "- Loan  DEFAULT\nloan default\n  What is  default\n\nwhat is default\n"
    assert parse_variants(text, "loan default") == ["What is default"]


def test_parsing_takes_only_the_first_hyde_line_and_strips_its_prefix():
    assert parse_variants("answer: first\nANSWER: second", "q") == ["first"]
