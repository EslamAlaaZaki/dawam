"""The adapter contract suite (spec §6.18): every adapter must pass it.

It runs against recorded provider responses (``fixtures/<adapter>/``), so it needs no
network and no database. A new adapter adds an ``AdapterCase`` to ``ADAPTERS`` and
records the fixtures named below (``chat_text``, ``chat_stream``, ``tool_call``, ...);
the same checks then cover streaming, the tool-call round trip, error mapping and usage
reporting for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dawam.modules.llm import (
    Adapter,
    Done,
    LlmError,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    Usage,
)
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.azure_openai import AzureOpenAIAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import Transport, TransportError
from tests.llm.replay import Recorded, ReplayTransport, load

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class AdapterCase:
    name: str
    make: Callable[[Transport], Adapter]
    chat_url: str
    auth_header: tuple[str, str]
    tool_names: Callable[[Any], list[str]]
    """The names of the tools a recorded chat request offered."""
    round_trip: Callable[[Any], None]
    """Asserts the wire shape of the tool-call round trip request."""
    context_window: int | None = 32768
    """What the recorded ``models`` fixture reports for ``test-model``."""
    embeds: bool = True
    auth_is_prefix: bool = False
    """The auth header is signed per request, so only its start is fixed."""
    is_stream: Callable[[Recorded], bool] = lambda request: request.json["stream"] is True
    """Whether a recorded chat request asked for a stream."""
    embed_request: Callable[[Any], None] = lambda body: _openai_embed_request(body)
    """Asserts the wire shape of the embeddings request."""

    def auth_matches(self, headers: Any) -> bool:
        name, value = self.auth_header
        sent = headers[name]
        return sent.startswith(value) if self.auth_is_prefix else sent == value

    def replay(self, *names: str | TransportError) -> tuple[Adapter, ReplayTransport]:
        transport = ReplayTransport(
            *(n if isinstance(n, TransportError) else load(FIXTURES / self.name, n) for n in names)
        )
        return self.make(transport), transport


def _openai_round_trip(body: Any) -> None:
    sent = body["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"] == [
        {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"text": "ping"}'},
        }
    ]
    assert sent[3]["tool_call_id"] == "call_abc" and sent[3]["content"] == "ping"


def _anthropic_round_trip(body: Any) -> None:
    assert body["system"] == "Be brief."
    sent = body["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"] == [
        {"type": "tool_use", "id": "call_abc", "name": "echo", "input": {"text": "ping"}}
    ]
    assert sent[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_abc", "content": "ping"}
    ]


def _openai_embed_request(body: Any) -> None:
    assert body == {"model": "embed-model", "input": ["a", "b"]}


def _gemini_round_trip(body: Any) -> None:
    assert body["systemInstruction"] == {"parts": [{"text": "Be brief."}]}
    sent = body["contents"]
    assert [m["role"] for m in sent] == ["user", "model", "user"]
    assert sent[1]["parts"] == [
        {"functionCall": {"id": "call_abc", "name": "echo", "args": {"text": "ping"}}}
    ]
    assert sent[2]["parts"] == [
        {"functionResponse": {"id": "call_abc", "name": "echo", "response": {"result": "ping"}}}
    ]


def _gemini_embed_request(body: Any) -> None:
    assert body == {
        "requests": [
            {"model": "models/embed-model", "content": {"parts": [{"text": text}]}}
            for text in ("a", "b")
        ]
    }


def _bedrock_round_trip(body: Any) -> None:
    assert body["system"] == [{"text": "Be brief."}]
    sent = body["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"] == [
        {"toolUse": {"toolUseId": "call_abc", "name": "echo", "input": {"text": "ping"}}}
    ]
    assert sent[2]["content"] == [
        {"toolResult": {"toolUseId": "call_abc", "content": [{"text": "ping"}]}}
    ]


FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

ADAPTERS = [
    AdapterCase(
        "openai_compatible",
        lambda transport: OpenAICompatibleAdapter(
            base_url="http://llm.test/v1",
            api_key="sk-secret",
            timeout_seconds=9,
            transport=transport,
        ),
        chat_url="http://llm.test/v1/chat/completions",
        auth_header=("Authorization", "Bearer sk-secret"),
        tool_names=lambda body: [t["function"]["name"] for t in body["tools"]],
        round_trip=_openai_round_trip,
    ),
    AdapterCase(
        "anthropic",
        lambda transport: AnthropicAdapter(
            base_url="http://llm.test/v1",
            api_key="sk-secret",
            timeout_seconds=9,
            transport=transport,
        ),
        chat_url="http://llm.test/v1/messages",
        auth_header=("x-api-key", "sk-secret"),
        tool_names=lambda body: [t["name"] for t in body["tools"]],
        round_trip=_anthropic_round_trip,
        context_window=200000,
        embeds=False,
    ),
    AdapterCase(
        "azure_openai",
        lambda transport: AzureOpenAIAdapter(
            base_url="http://llm.test",
            api_key="sk-secret",
            timeout_seconds=9,
            transport=transport,
        ),
        chat_url="http://llm.test/openai/deployments/test-model/chat/completions"
        "?api-version=2024-10-21",
        auth_header=("api-key", "sk-secret"),
        tool_names=lambda body: [t["function"]["name"] for t in body["tools"]],
        round_trip=_openai_round_trip,
    ),
    AdapterCase(
        "gemini",
        lambda transport: GeminiAdapter(
            base_url="http://llm.test/v1beta",
            api_key="sk-secret",
            timeout_seconds=9,
            transport=transport,
        ),
        chat_url="http://llm.test/v1beta/models/test-model:generateContent",
        auth_header=("x-goog-api-key", "sk-secret"),
        tool_names=lambda body: [
            d["name"] for t in body["tools"] for d in t["functionDeclarations"]
        ],
        round_trip=_gemini_round_trip,
        is_stream=lambda request: request.url.endswith(":streamGenerateContent?alt=sse"),
        embed_request=_gemini_embed_request,
    ),
    AdapterCase(
        "bedrock",
        lambda transport: BedrockAdapter(
            base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
            api_key="AKIDEXAMPLE:sk-secret",
            timeout_seconds=9,
            transport=transport,
            now=lambda: FIXED_NOW,
        ),
        chat_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/test-model/converse",
        auth_header=(
            "Authorization",
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260102/us-east-1/bedrock/aws4_request",
        ),
        auth_is_prefix=True,
        tool_names=lambda body: [t["toolSpec"]["name"] for t in body["toolConfig"]["tools"]],
        round_trip=_bedrock_round_trip,
        context_window=None,
        embeds=False,
        is_stream=lambda request: request.url.endswith("/converse-stream"),
    ),
]


@pytest.fixture(params=ADAPTERS, ids=lambda case: case.name)
def case(request: pytest.FixtureRequest) -> AdapterCase:
    return request.param


ASK = [Message("user", "Hi")]
ECHO = ToolSpec(
    "echo",
    "Repeat the text.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)


def run(adapter: Adapter, *, stream: bool = False, tools=(), messages=ASK):
    return list(adapter.chat("test-model", messages, tools, stream, None))


def test_a_reply_is_normalised_with_the_usage_the_provider_reported(case):
    adapter, _ = case.replay("chat_text")

    assert run(adapter) == [TextDelta("Hello there."), Done(Usage(12, 3), "stop")]


def test_a_reply_without_usage_reports_none_so_the_gateway_can_estimate(case):
    adapter, _ = case.replay("chat_text_no_usage")

    assert run(adapter)[-1] == Done(None, "stop")


def test_a_stream_yields_text_deltas_then_done_with_usage(case):
    adapter, transport = case.replay("chat_stream")

    events = run(adapter, stream=True)

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello", " there."]
    assert events[-1] == Done(Usage(12, 3), "stop")
    assert case.is_stream(transport.requests[0])


def test_a_stream_without_usage_ends_with_done_and_no_usage(case):
    adapter, _ = case.replay("chat_stream_no_usage")

    assert run(adapter, stream=True)[-1] == Done(None, "stop")


def test_a_tool_call_arrives_complete_with_parsed_arguments(case):
    adapter, transport = case.replay("tool_call")

    events = run(adapter, tools=[ECHO])

    assert events[0] == ToolCallEvent(ToolCall("call_abc", "echo", {"text": "ping"}))
    assert events[-1] == Done(Usage(40, 9), "tool_calls")
    assert case.tool_names(transport.requests[0].json) == ["echo"]


def test_a_streamed_tool_call_is_assembled_from_its_fragments(case):
    adapter, _ = case.replay("tool_call_stream")

    events = run(adapter, stream=True, tools=[ECHO])

    assert events == [
        ToolCallEvent(ToolCall("call_abc", "echo", {"text": "ping"})),
        Done(Usage(40, 9), "tool_calls"),
    ]


def test_a_tool_call_round_trip_sends_the_call_and_its_result_back(case):
    adapter, transport = case.replay("chat_text")
    call = ToolCall("call_abc", "echo", {"text": "ping"})
    conversation = [
        Message("system", "Be brief."),
        Message("user", "Echo ping"),
        Message("assistant", tool_calls=(call,)),
        Message("tool", "ping", tool_call_id="call_abc"),
    ]

    events = run(adapter, tools=[ECHO], messages=conversation)

    assert events[0] == TextDelta("Hello there.")
    case.round_trip(transport.requests[0].json)


@pytest.mark.parametrize(
    ("fixture", "code", "retryable"),
    [
        ("error_auth", "auth", False),
        ("error_rate_limit", "rate_limit", True),
        ("error_context", "context_overflow", False),
        ("error_unavailable", "unavailable", True),
        ("error_bad_request", "bad_request", False),
    ],
)
def test_provider_errors_map_to_the_common_codes(case, fixture, code, retryable):
    adapter, _ = case.replay(fixture)

    with pytest.raises(LlmError) as raised:
        run(adapter)

    assert raised.value.code == code
    assert raised.value.retryable is retryable


def test_a_rate_limit_carries_the_wait_the_provider_asked_for(case):
    adapter, _ = case.replay("error_rate_limit")

    with pytest.raises(LlmError) as raised:
        run(adapter)

    assert raised.value.retry_after == 7


def test_an_unreachable_provider_is_unavailable(case):
    adapter, _ = case.replay(TransportError("The provider could not be reached."))

    with pytest.raises(LlmError) as raised:
        run(adapter)

    assert raised.value.code == "unavailable"


def test_an_error_message_never_contains_the_api_key(case):
    adapter, _ = case.replay("error_auth")

    with pytest.raises(LlmError) as raised:
        run(adapter)

    assert "sk-secret" not in raised.value.message


def test_embeddings_come_back_one_per_text_in_order(case):
    if not case.embeds:
        pytest.skip("this adapter has no embeddings")
    adapter, transport = case.replay("embed")

    vectors = adapter.embed("embed-model", ["a", "b"])

    assert vectors == [[1.0, 0.0, 0.25], [0.0, 1.0, 0.5]]
    case.embed_request(transport.requests[0].json)


def test_requests_carry_the_key_and_the_provider_timeout(case):
    adapter, transport = case.replay("chat_text")

    run(adapter)

    request = transport.requests[0]
    assert request.url == case.chat_url
    assert case.auth_matches(request.headers)
    assert request.timeout == 9


def test_the_context_window_comes_from_the_servers_metadata_when_it_has_one(case):
    adapter, _ = case.replay("models")
    assert adapter.context_window("test-model") == case.context_window

    adapter, _ = case.replay("models_without_metadata")
    assert adapter.context_window("test-model") is None

    adapter, _ = case.replay("error_unavailable")
    assert adapter.context_window("test-model") is None


def test_an_adapter_without_embeddings_refuses_them_as_a_bad_request(case):
    if case.embeds:
        pytest.skip("this adapter has embeddings")
    adapter, transport = case.replay()

    with pytest.raises(LlmError) as raised:
        adapter.embed("embed-model", ["a"])

    assert raised.value.code == "bad_request" and transport.requests == []
