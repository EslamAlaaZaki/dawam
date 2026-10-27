"""Reasoning models (#409): thinking settings per provider, reasoning streamed or hidden, and
its tokens counted.

The Anthropic and OpenAI replies below are built strictly from the providers' documented
request and response formats (no API key was available to record them): re-record with a
real key. The vLLM / Ollama / DeepSeek shapes come from ``StubLlmServer``.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from dawam.modules.llm import (
    Done,
    Gateway,
    Message,
    ReasoningDelta,
    ReasoningSettings,
    TextDelta,
    Usage,
)
from dawam.modules.llm.internal.anthropic import DEFAULT_MAX_TOKENS, AnthropicAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.think_tags import ThinkTagSplitter
from dawam.modules.llm.internal.transport import HttpResponse, UrllibTransport
from tests.llm.replay import ReplayTransport
from tests.llm.stub_server import StubLlmServer

ASK = [Message("user", "Why is the sky blue?")]


def _json(body: Any) -> HttpResponse:
    return HttpResponse(200, {}, io.BytesIO(json.dumps(body).encode()))


def _sse(*events: dict[str, Any]) -> HttpResponse:
    raw = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    return HttpResponse(200, {}, io.BytesIO(raw))


def _anthropic(response: HttpResponse) -> tuple[AnthropicAdapter, ReplayTransport]:
    transport = ReplayTransport(response)
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


def _openai(response: HttpResponse) -> tuple[OpenAICompatibleAdapter, ReplayTransport]:
    transport = ReplayTransport(response)
    adapter = OpenAICompatibleAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


# -- Anthropic extended thinking ------------------------------------------------------

THINKING_REPLY = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-5",
    "content": [
        {"type": "thinking", "thinking": "Rayleigh scattering.", "signature": "sig"},
        {"type": "text", "text": "Short wavelengths scatter more."},
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 40},
}


def test_anthropic_asks_for_extended_thinking_with_a_budget_inside_max_tokens():
    adapter, transport = _anthropic(_json(THINKING_REPLY))

    list(adapter.chat("m", ASK, [], False, None, reasoning=ReasoningSettings(budget_tokens=2000)))

    body = transport.requests[0].json
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2000}
    assert body["max_tokens"] == 2000 + DEFAULT_MAX_TOKENS


def test_anthropic_maps_an_effort_to_a_budget_and_never_goes_below_the_minimum():
    adapter, transport = _anthropic(_json(THINKING_REPLY))
    list(adapter.chat("m", ASK, [], False, None, reasoning=ReasoningSettings(effort="high")))
    assert transport.requests[0].json["thinking"]["budget_tokens"] == 8192

    adapter, transport = _anthropic(_json(THINKING_REPLY))
    list(adapter.chat("m", ASK, [], False, None, reasoning=ReasoningSettings(budget_tokens=10)))
    assert transport.requests[0].json["thinking"]["budget_tokens"] == 1024


def test_anthropic_sends_no_thinking_without_settings():
    adapter, transport = _anthropic(_json(THINKING_REPLY))

    list(adapter.chat("m", ASK, [], False, None))

    assert "thinking" not in transport.requests[0].json


def test_anthropic_skips_thinking_once_the_run_has_called_a_tool():
    from dawam.modules.llm import ToolCall

    history = [*ASK, Message("assistant", None, (ToolCall("t1", "echo", {}),))]
    history.append(Message("tool", "ok", tool_call_id="t1"))
    adapter, transport = _anthropic(_json(THINKING_REPLY))

    list(adapter.chat("m", history, [], False, None, reasoning=ReasoningSettings(effort="low")))

    assert "thinking" not in transport.requests[0].json


def test_anthropic_thinking_blocks_become_reasoning_deltas():
    adapter, _ = _anthropic(_json(THINKING_REPLY))

    events = list(adapter.chat("m", ASK, [], False, None, reasoning=ReasoningSettings()))

    assert events[0] == ReasoningDelta("Rayleigh scattering.")
    assert events[1] == TextDelta("Short wavelengths scatter more.")


def test_anthropic_streams_thinking_deltas():
    adapter, _ = _anthropic(
        _sse(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 9, "output_tokens": 1}},
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Rayleigh "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "scattering."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Blue."},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 30},
            },
            {"type": "message_stop"},
        )
    )

    events = list(adapter.chat("m", ASK, [], True, None, reasoning=ReasoningSettings()))

    assert [e for e in events if isinstance(e, ReasoningDelta)] == [
        ReasoningDelta("Rayleigh "),
        ReasoningDelta("scattering."),
    ]
    assert TextDelta("Blue.") in events


def test_gateway_hides_reasoning_by_default_but_counts_it():
    adapter, _ = _anthropic(_json(THINKING_REPLY))
    gateway = Gateway(adapter, model="m", reasoning=ReasoningSettings(budget_tokens=2000))

    events = list(gateway.chat(ASK))

    assert not any(isinstance(e, ReasoningDelta) for e in events)
    done = events[-1]
    assert isinstance(done, Done) and done.usage is not None
    assert done.usage.completion_tokens == 40  # the provider's figure: thinking is inside it
    assert done.usage.reasoning_tokens == len("Rayleigh scattering.") // 4


def test_gateway_streams_reasoning_when_the_setting_shows_it():
    adapter, _ = _anthropic(_json(THINKING_REPLY))
    gateway = Gateway(adapter, model="m", reasoning=ReasoningSettings(show=True))

    events = list(gateway.chat(ASK))

    assert ReasoningDelta("Rayleigh scattering.") in events


# -- OpenAI reasoning effort ----------------------------------------------------------


def test_openai_sends_reasoning_effort_and_reports_reasoning_tokens():
    adapter, transport = _openai(
        _json(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Blue."}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 300,
                    "completion_tokens_details": {"reasoning_tokens": 256},
                },
            }
        )
    )

    events = list(
        adapter.chat("o4-mini", ASK, [], False, None, reasoning=ReasoningSettings(effort="low"))
    )

    assert transport.requests[0].json["reasoning_effort"] == "low"
    assert events[-1] == Done(Usage(10, 300, reasoning_tokens=256), "stop")


def test_openai_sends_nothing_extra_without_settings():
    adapter, transport = _openai(
        _json({"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})
    )

    list(adapter.chat("gpt", ASK, [], False, None))

    assert "reasoning_effort" not in transport.requests[0].json


def test_gateway_estimates_reasoning_when_a_provider_reports_no_usage():
    adapter, _ = _openai(
        _json(
            {
                "choices": [
                    {
                        "message": {"content": "Blue.", "reasoning_content": "x" * 40},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    )
    gateway = Gateway(adapter, model="m", reasoning=ReasoningSettings())

    done = list(gateway.chat(ASK))[-1]

    assert isinstance(done, Done) and done.usage is not None
    assert done.usage.estimated and done.usage.reasoning_tokens == 10
    assert done.usage.completion_tokens == 10 + 1  # the reasoning plus "Blue."


# -- <think> tags ----------------------------------------------------------------------


def test_think_tags_split_across_chunks():
    splitter = ThinkTagSplitter()
    pieces = [
        *splitter.feed("<thi"),
        *splitter.feed("nk>plan</th"),
        *splitter.feed("ink>answer"),
        *splitter.flush(),
    ]
    assert "".join(t for r, t in pieces if r) == "plan"
    assert "".join(t for r, t in pieces if not r) == "answer"


def test_a_lone_angle_bracket_is_not_swallowed():
    splitter = ThinkTagSplitter()
    pieces = [*splitter.feed("a <b"), *splitter.flush()]
    assert "".join(t for _, t in pieces) == "a <b"


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("stream", [False, True])
def test_vllm_and_ollama_send_reasoning_beside_the_content(field: str, stream: bool):
    server = StubLlmServer()
    server.reasoning_field = field
    with server.running():
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, api_key=None, timeout_seconds=10, transport=UrllibTransport()
        )
        gateway = Gateway(adapter, model="stub", reasoning=ReasoningSettings(show=True))
        events = list(gateway.chat(ASK, stream=stream))

    assert "".join(e.text for e in events if isinstance(e, ReasoningDelta)) == "Let me think."
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "OK"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("show", [False, True])
def test_qwen3_and_deepseek_r1_think_tags_are_split_out_of_the_content(stream: bool, show: bool):
    server = StubLlmServer()
    server.inline_think = True
    with server.running():
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, api_key=None, timeout_seconds=10, transport=UrllibTransport()
        )
        gateway = Gateway(adapter, model="stub", reasoning=ReasoningSettings(show=show))
        events = list(gateway.chat(ASK, stream=stream))

    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "OK"
    thought = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    assert thought == ("Let me think." if show else "")
    done = events[-1]
    assert isinstance(done, Done) and done.usage is not None
    assert 0 < done.usage.reasoning_tokens <= done.usage.completion_tokens
