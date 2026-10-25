"""The ``rerank`` model role (#373): the adapter contract for ``POST /rerank`` (Cohere, Jina and
vLLM/self-hosted ``bge-reranker-v2-m3`` shapes), the gateway's retries and the Test connection
probe, and the metered gateway's budgets and usage. No real model is called.

The three ``rerank_*`` fixtures are built strictly from each provider's documented response
format (no key was available to record them): re-record them with a real key.
"""

from __future__ import annotations

import pytest

from dawam.modules.llm import Gateway, LlmError
from dawam.modules.llm.gateway import probe
from dawam.modules.llm.internal.fake import FakeAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import UrllibTransport
from tests.llm.replay import ReplayTransport, load
from tests.llm.stub_server import StubLlmServer
from tests.llm.test_adapter_contract import ADAPTERS, FIXTURES, AdapterCase
from tests.llm.test_roles_budgets import BASE, register

OPENAI = next(c for c in ADAPTERS if c.name == "openai_compatible")


def adapter_for(*names: str) -> tuple[OpenAICompatibleAdapter, ReplayTransport]:
    transport = ReplayTransport(*(load(FIXTURES / "openai_compatible", n) for n in names))
    adapter = OPENAI.make(transport)
    assert isinstance(adapter, OpenAICompatibleAdapter)
    return adapter, transport


@pytest.mark.parametrize("fixture", ["rerank_cohere", "rerank_jina", "rerank_vllm"])
def test_scores_come_back_one_per_document_in_the_order_sent(fixture):
    adapter, transport = adapter_for(fixture)

    scores = adapter.rerank("rerank-model", "what is a posting?", ["a", "b"])

    assert scores[0] < scores[1] and len(scores) == 2
    [request] = transport.requests
    assert request.url == "http://llm.test/v1/rerank"
    assert request.json == {
        "model": "rerank-model",
        "query": "what is a posting?",
        "documents": ["a", "b"],
    }
    assert OPENAI.auth_matches(request.headers)


def test_a_reply_without_scores_or_with_missing_documents_is_invalid():
    for fixture in ("rerank_malformed", "rerank_partial", "rerank_nan", "rerank_inf"):
        adapter, _ = adapter_for(fixture)
        with pytest.raises(LlmError) as raised:
            adapter.rerank("rerank-model", "q", ["a", "b"])
        assert raised.value.code == "invalid_response"


def test_provider_errors_map_to_the_common_codes():
    adapter, _ = adapter_for("error_rate_limit")
    with pytest.raises(LlmError) as raised:
        adapter.rerank("rerank-model", "q", ["a"])
    assert raised.value.code == "rate_limit"


@pytest.mark.parametrize("case", [c for c in ADAPTERS if c is not OPENAI], ids=lambda c: c.name)
def test_an_adapter_without_a_rerank_endpoint_refuses_it_as_a_bad_request(case: AdapterCase):
    adapter, transport = case.replay()

    gateway = Gateway(adapter, model="m")
    with pytest.raises(LlmError) as raised:
        gateway.rerank("q", ["a"])

    assert raised.value.code == "bad_request" and transport.requests == []


def test_the_gateway_retries_a_rate_limited_rerank_call():
    fake = FakeAdapter().script_rerank(LlmError("rate_limit", "slow down"), [0.2, 0.8])
    gateway = Gateway(fake, model="m", sleep=lambda seconds: None)

    assert gateway.rerank("q", ["a", "b"]) == [0.2, 0.8]
    assert [c.kind for c in fake.calls] == ["rerank", "rerank"]


def test_test_connection_ranks_two_documents():
    fake = FakeAdapter()

    result = probe(Gateway(fake, model="m"), chat=False, embedding=False, rerank=True)

    assert result.ok
    [call] = fake.calls
    assert call.kind == "rerank" and len(call.texts) == 2


def test_test_connection_fails_when_the_model_cannot_rerank():
    gateway = Gateway(FakeAdapter().script_rerank(LlmError("bad_request", "no")), model="m")

    result = probe(gateway, chat=False, embedding=False, rerank=True)

    assert not result.ok and result.error_code == "bad_request"


def test_a_rerank_model_over_real_http_is_tested_and_never_asked_to_chat(admin_client, fake_llm):
    model = register(admin_client, "bge-reranker-v2-m3", roles=("rerank",), tested=False)

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is True, tested
    assert tested["roles"] == ["rerank"]
    assert {c.kind for c in fake_llm.calls} == {"rerank"}


def test_a_server_without_a_rerank_endpoint_is_refused_with_a_bad_request():
    """A self-hosted server without ``/rerank`` (plain Ollama) fails the test, not the app."""
    server = StubLlmServer()
    server.fail_with = 404
    with server.running():
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, api_key=None, timeout_seconds=5, transport=UrllibTransport()
        )
        with pytest.raises(LlmError) as raised:
            adapter.rerank("m", "q", ["a"])
    assert raised.value.code == "bad_request"
    assert server.requests[0]["path"].endswith("/rerank")
