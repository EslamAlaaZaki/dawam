"""A mid-stream error chunk that says the model's tool call broke the tool's schema is
``invalid_tool_call``; any other error chunk stays ``unavailable`` (ticket #615)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from dawam.modules.llm import LlmError, Message
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import HttpResponse
from tests.llm.replay import eventstream_frame

REJECTED = "Tool call validation failed: arguments did not match schema for tool 'lookup'"
OTHER = "The upstream server is overloaded"


class Canned:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def request(self, method: str, url: str, *, headers: Any, body: Any, timeout: Any):
        return HttpResponse(200, {}, io.BytesIO(self.body))


def sse(*payloads: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(p).encode() + b"\n\n" for p in payloads)


def chat(adapter: Any) -> None:
    list(adapter.chat("m", [Message("user", "Hi")], [], True, None))


def openai(error: dict[str, Any]) -> Any:
    chunks = [{"choices": [{"delta": {"content": "x"}}]}, error]
    return OpenAICompatibleAdapter(
        base_url="http://llm.test/v1",
        api_key="k",
        timeout_seconds=9,
        transport=Canned(sse(*chunks)),
    )


def anthropic(error: dict[str, Any]) -> Any:
    return AnthropicAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=Canned(sse(error))
    )


def gemini(error: dict[str, Any]) -> Any:
    return GeminiAdapter(
        base_url="https://g.test", api_key="k", timeout_seconds=9, transport=Canned(sse(error))
    )


def bedrock(error: dict[str, Any]) -> Any:
    frame = eventstream_frame("internalServerException", error, "exception")
    return BedrockAdapter(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key="AKID:SECRET",
        timeout_seconds=9,
        transport=Canned(frame),
    )


SHAPES = {
    "openai": (openai, lambda m: {"error": {"message": m}}),
    "anthropic": (anthropic, lambda m: {"type": "error", "error": {"message": m}}),
    "gemini": (gemini, lambda m: {"error": {"message": m}}),
    "bedrock": (bedrock, lambda m: {"message": m}),
}


@pytest.mark.parametrize("name", SHAPES)
def test_a_tool_call_validation_chunk_is_an_invalid_tool_call(name):
    build, shape = SHAPES[name]

    with pytest.raises(LlmError) as raised:
        chat(build(shape(REJECTED)))

    assert raised.value.code == "invalid_tool_call"
    assert "did not match schema" in raised.value.message
    assert not raised.value.retryable


@pytest.mark.parametrize("name", SHAPES)
def test_any_other_error_chunk_stays_unavailable(name):
    build, shape = SHAPES[name]

    with pytest.raises(LlmError) as raised:
        chat(build(shape(OTHER)))

    assert raised.value.code == "unavailable"
