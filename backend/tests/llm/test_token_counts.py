"""Real token counts: the provider's count endpoint where one exists, else the estimate.

The count fixtures are built from the providers' documented formats (Anthropic
``/messages/count_tokens`` -> ``input_tokens``; Gemini ``:countTokens`` -> ``totalTokens``),
not recorded: re-record them with a real key.
"""

from __future__ import annotations

from pathlib import Path

from dawam.modules.assistant.internal.context import fit_messages
from dawam.modules.llm import FakeAdapter, Gateway, LlmError, Message
from dawam.modules.llm.gateway import estimate_tokens
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import TransportError, UrllibTransport
from tests.llm.replay import ReplayTransport, load
from tests.llm.stub_server import StubLlmServer

FIXTURES = Path(__file__).parent / "fixtures"
ARABIC = "مرحبا بكم في داوم، كيف يمكنني مساعدتكم اليوم؟"


def _replay(directory: str, *names: str | TransportError) -> ReplayTransport:
    return ReplayTransport(
        *(n if isinstance(n, TransportError) else load(FIXTURES / directory, n) for n in names)
    )


def test_anthropic_counts_through_its_count_tokens_endpoint():
    transport = _replay("anthropic", "count_tokens")
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )

    assert adapter.count_tokens("test-model", ARABIC) == 14

    request = transport.requests[0]
    assert request.url == "http://llm.test/v1/messages/count_tokens"
    assert request.json == {
        "model": "test-model",
        "messages": [{"role": "user", "content": ARABIC}],
    }


def test_gemini_counts_through_its_count_tokens_method():
    transport = _replay("gemini", "count_tokens")
    adapter = GeminiAdapter(
        base_url="http://llm.test/v1beta", api_key="k", timeout_seconds=9, transport=transport
    )

    assert adapter.count_tokens("test-model", ARABIC) == 14

    request = transport.requests[0]
    assert request.url == "http://llm.test/v1beta/models/test-model:countTokens"
    assert request.json == {"contents": [{"role": "user", "parts": [{"text": ARABIC}]}]}


def test_a_failing_count_endpoint_gives_none_not_an_error():
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1",
        api_key="k",
        timeout_seconds=9,
        transport=_replay("anthropic", "error_unavailable", TransportError("down")),
    )

    assert adapter.count_tokens("m", "hi") is None
    assert adapter.count_tokens("m", "hi") is None


def test_the_gateway_uses_the_providers_count_and_falls_back_to_the_estimate():
    fake = FakeAdapter()
    llm = Gateway(fake, model="m")
    text = "x" * 400

    assert llm.count_tokens(text) == estimate_tokens(text) == 100  # no count endpoint

    fake.token_counter = lambda t: 7
    assert llm.count_tokens(text) == 7

    def broken(t: str) -> int:
        raise LlmError("unavailable", "down")

    fake.token_counter = broken
    assert llm.count_tokens(text) == 100  # a failing endpoint never fails the caller


def test_a_self_hosted_model_is_counted_without_any_network_call():
    with StubLlmServer().running() as server:
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url,
            api_key=None,
            timeout_seconds=5,
            transport=UrllibTransport(),
        )

        assert Gateway(adapter, model="stub-model").count_tokens("x" * 40) == 10

        assert server.requests == []


def test_context_fitting_uses_the_real_count_so_arabic_is_not_under_counted():
    # chars/4 thinks this fits; a real tokenizer counts 3 tokens per 4 characters here.
    history = [Message("user" if i % 2 == 0 else "assistant", ARABIC * 10) for i in range(6)] + [
        Message("user", "LAST")
    ]
    messages = [Message("system", "SYS"), *history]
    window = 600

    estimated = fit_messages(messages, window, protect_from=len(history))
    real = fit_messages(
        messages, window, protect_from=len(history), count=lambda text: len(text) * 3 // 4
    )

    assert len(real) < len(estimated)
    assert real[-1].content == "LAST"
