"""What is specific to the Anthropic wire format (the shared behaviour is in the contract suite)."""

from __future__ import annotations

from pathlib import Path

from dawam.modules.llm import Capabilities, Gateway, Message, ToolSpec
from dawam.modules.llm.gateway import probe
from dawam.modules.llm.internal.anthropic import DEFAULT_MAX_TOKENS, AnthropicAdapter
from tests.llm.replay import ReplayTransport, load

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def _adapter(*names: str) -> tuple[AnthropicAdapter, ReplayTransport]:
    transport = ReplayTransport(*(load(FIXTURES, n) for n in names))
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1/", api_key="sk-secret", timeout_seconds=9, transport=transport
    )
    return adapter, transport


def test_requests_carry_the_api_version_and_a_max_tokens_budget():
    adapter, transport = _adapter("chat_text")

    list(adapter.chat("m", [Message("user", "Hi")], [], False, None))

    request = transport.requests[0]
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.json["max_tokens"] == DEFAULT_MAX_TOKENS
    assert request.json["model"] == "m" and request.json["stream"] is False


def test_tools_use_input_schema_and_a_schema_request_uses_structured_output():
    adapter, transport = _adapter("chat_text")
    tool = ToolSpec("echo", "Repeat.", {"type": "object", "properties": {}})
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    list(adapter.chat("m", [Message("user", "Hi")], [tool], False, schema))

    body = transport.requests[0].json
    assert body["tools"] == [
        {"name": "echo", "description": "Repeat.", "input_schema": tool.parameters}
    ]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema}}


def test_consecutive_tool_results_share_one_user_message():
    adapter, transport = _adapter("chat_text")
    messages = [
        Message("user", "Go"),
        Message("tool", "a", tool_call_id="1"),
        Message("tool", "b", tool_call_id="2"),
    ]

    list(adapter.chat("m", messages, [], False, None))

    sent = transport.requests[0].json["messages"]
    assert [m["role"] for m in sent] == ["user"]
    assert [b.get("tool_use_id") for b in sent[0]["content"] if b["type"] == "tool_result"] == [
        "1",
        "2",
    ]


def test_the_context_window_asks_for_the_model_by_name():
    adapter, transport = _adapter("models")

    assert adapter.context_window("test-model") == 200000
    assert transport.requests[0].url == "http://llm.test/v1/models/test-model"


def test_test_connection_detects_capabilities_through_the_gateway():
    adapter, _ = _adapter("chat_text", "chat_stream", "tool_call", "chat_text", "models")

    result = probe(Gateway(adapter, model="test-model"), chat=True, embedding=False)

    assert result.ok is True
    assert result.capabilities == Capabilities(
        tool_calling=True, streaming=True, json_schema=False, context_window=200000
    )
