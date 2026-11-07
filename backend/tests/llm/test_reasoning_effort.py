"""The effort reaches every provider (#555): Anthropic adaptive thinking or a budget by model,
Gemini thinking levels and budgets, Claude on Bedrock, and ``<think>`` tags always split.

The replies below are built strictly from each provider's documented request and response
format (Anthropic Messages and Models APIs, Gemini ``generateContent`` and ``models.get``,
Bedrock Converse, Ollama ``/api/show``, OpenRouter ``/models``): no API key was available
to record them. Re-record with a real key.
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
    ToolCall,
)
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.thinking import claude_adaptive
from dawam.modules.llm.internal.transport import HttpResponse, UrllibTransport
from tests.llm.replay import ReplayTransport, eventstream_frame
from tests.llm.stub_server import StubLlmServer

ASK = [Message("user", "Why is the sky blue?")]
BINDING = {"prefix_mismatch_behavior": "drop_block"}
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _json(body: Any, status: int = 200) -> HttpResponse:
    return HttpResponse(status, {}, io.BytesIO(json.dumps(body).encode()))


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_contains_key(v, key) for v in value)
    return False


# -- Anthropic ---------------------------------------------------------------------------

CLAUDE_REPLY = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Blue."},
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 40},
}


def _anthropic(*responses: HttpResponse) -> tuple[AnthropicAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


@pytest.mark.parametrize(
    ("model", "adaptive"),
    [
        ("claude-opus-5-5", True),
        ("claude-sonnet-5-5", True),
        ("claude-haiku-5-5", True),
        ("claude-opus-4-7", True),
        ("claude-opus-4-6", True),
        ("claude-sonnet-4-6", True),
        ("claude-fable-5-1", True),
        ("claude-opus-5", True),
        ("us.anthropic.claude-opus-4-7-v1", True),
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-5-20250929", False),
        ("claude-opus-4-20250514", False),
        ("claude-3-7-sonnet-20250219", False),
        ("corp-model", None),
    ],
)
def test_the_thinking_shape_is_picked_from_the_model_id(model: str, adaptive: bool | None):
    assert claude_adaptive(model) is adaptive


def test_an_adaptive_model_gets_adaptive_thinking_and_the_effort_beside_the_schema():
    adapter, transport = _anthropic(_json(CLAUDE_REPLY))

    list(
        adapter.chat(
            "claude-opus-5-5", ASK, [], False, SCHEMA, reasoning=ReasoningSettings(effort="high")
        )
    )

    body = transport.requests[0].json
    assert body["thinking"] == {"type": "adaptive", "block_binding": BINDING}
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA},
        "effort": "high",
    }
    assert not _contains_key(body, "budget_tokens")


def test_an_adaptive_model_without_an_effort_gets_the_providers_default():
    adapter, transport = _anthropic(_json(CLAUDE_REPLY))

    list(adapter.chat("claude-sonnet-5-5", ASK, [], False, None, reasoning=ReasoningSettings()))

    body = transport.requests[0].json
    assert body["thinking"] == {"type": "adaptive", "block_binding": BINDING}
    assert "output_config" not in body


def test_showing_the_reasoning_asks_an_adaptive_model_for_its_summary():
    adapter, transport = _anthropic(_json(CLAUDE_REPLY))

    list(
        adapter.chat(
            "claude-opus-4-7", ASK, [], False, None, reasoning=ReasoningSettings(show=True)
        )
    )

    assert transport.requests[0].json["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": BINDING,
    }


def test_an_older_model_keeps_the_budget_from_the_effort():
    adapter, transport = _anthropic(_json(CLAUDE_REPLY))

    list(
        adapter.chat(
            "claude-haiku-4-5", ASK, [], False, None, reasoning=ReasoningSettings(effort="medium")
        )
    )

    body = transport.requests[0].json
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096, "block_binding": BINDING}
    assert "output_config" not in body


def test_after_a_tool_call_an_adaptive_model_keeps_thinking_and_its_effort():
    history = [
        *ASK,
        Message(
            "assistant",
            None,
            (ToolCall("t1", "echo", {}),),
            thinking=({"type": "thinking", "thinking": "", "signature": "sig"},),
        ),
        Message("tool", "ok", tool_call_id="t1"),
    ]
    adapter, transport = _anthropic(_json(CLAUDE_REPLY))

    list(
        adapter.chat(
            "claude-opus-5-5", history, [], False, None, reasoning=ReasoningSettings(effort="low")
        )
    )

    body = transport.requests[0].json
    assert body["thinking"] == {"type": "adaptive", "block_binding": BINDING}
    assert body["output_config"] == {"effort": "low"}


def _anthropic_model(adaptive: bool, enabled: bool) -> dict[str, Any]:
    return {
        "type": "model",
        "id": "corp-sonnet",
        "display_name": "Corp Sonnet",
        "max_input_tokens": 1000000,
        "max_tokens": 128000,
        "capabilities": {
            "thinking": {
                "supported": adaptive or enabled,
                "types": {"adaptive": {"supported": adaptive}, "enabled": {"supported": enabled}},
            },
            "effort": {"supported": adaptive, "low": {"supported": adaptive}},
        },
    }


def test_an_unknown_model_id_is_looked_up_once_in_the_models_api():
    adapter, transport = _anthropic(
        _json(_anthropic_model(adaptive=True, enabled=False)),
        _json(CLAUDE_REPLY),
        _json(CLAUDE_REPLY),
    )
    settings = ReasoningSettings(effort="low")

    list(adapter.chat("corp-sonnet", ASK, [], False, None, reasoning=settings))
    list(adapter.chat("corp-sonnet", ASK, [], False, None, reasoning=settings))

    first, second, third = transport.requests
    assert (first.method, first.url) == ("GET", "http://llm.test/v1/models/corp-sonnet")
    assert second.json["thinking"]["type"] == "adaptive" and third.method == "POST"


def test_an_unknown_model_the_models_api_cannot_tell_keeps_the_budget():
    adapter, transport = _anthropic(_json({"error": {}}, status=404), _json(CLAUDE_REPLY))

    list(
        adapter.chat("corp-sonnet", ASK, [], False, None, reasoning=ReasoningSettings(effort="low"))
    )

    assert transport.requests[1].json["thinking"]["budget_tokens"] == 1024


# -- Gemini ------------------------------------------------------------------------------

GEMINI_REPLY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "Rayleigh.", "thought": True}, {"text": "Blue."}],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 8,
        "candidatesTokenCount": 2,
        "thoughtsTokenCount": 120,
        "totalTokenCount": 130,
    },
}


def _gemini(*responses: HttpResponse) -> tuple[GeminiAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = GeminiAdapter(
        base_url="https://g.test/v1beta", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


@pytest.mark.parametrize(
    ("model", "effort", "level"),
    [
        ("gemini-3-flash-preview", "low", "low"),
        ("gemini-3-flash-preview", "medium", "medium"),
        ("gemini-3-flash-preview", "high", "high"),
        ("gemini-3-pro-preview", "low", "low"),
        ("gemini-3-pro-preview", "medium", "high"),  # Gemini 3 Pro has no medium: step up
        ("gemini-3.1-pro-preview", "medium", "medium"),
    ],
)
def test_gemini_3_gets_the_effort_as_its_thinking_level(model: str, effort: str, level: str):
    adapter, transport = _gemini(_json(GEMINI_REPLY))

    list(adapter.chat(model, ASK, [], False, SCHEMA, reasoning=ReasoningSettings(effort=effort)))

    config = transport.requests[0].json["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingLevel": level}
    assert config["responseJsonSchema"] == SCHEMA


@pytest.mark.parametrize(("effort", "budget"), [("low", 1024), ("medium", 4096), ("high", 8192)])
def test_gemini_2_5_gets_a_thinking_budget_from_the_effort(effort: str, budget: int):
    adapter, transport = _gemini(_json(GEMINI_REPLY))

    list(
        adapter.chat(
            "gemini-2.5-flash", ASK, [], False, None, reasoning=ReasoningSettings(effort=effort)
        )
    )

    thinking = transport.requests[0].json["generationConfig"]["thinkingConfig"]
    assert thinking == {"thinkingBudget": budget}


@pytest.mark.parametrize("model", ["gemini-2.0-flash", "gemini-flash-latest", "corp-gemini"])
def test_gemini_models_outside_2_5_and_3_get_no_thinking_config(model: str):
    adapter, transport = _gemini(_json(GEMINI_REPLY))
    settings = ReasoningSettings(effort="high", budget_tokens=2000, show=True)

    list(adapter.chat(model, ASK, [], False, None, reasoning=settings))

    assert "generationConfig" not in transport.requests[0].json


def test_gemini_without_settings_sends_no_thinking_config():
    adapter, transport = _gemini(_json(GEMINI_REPLY))

    list(adapter.chat("gemini-3-pro-preview", ASK, [], False, None))

    assert "generationConfig" not in transport.requests[0].json


def test_gemini_thought_tokens_are_reasoning_tokens_and_thoughts_show_only_when_asked():
    adapter, transport = _gemini(_json(GEMINI_REPLY), _json(GEMINI_REPLY))

    hidden = list(
        Gateway(adapter, model="gemini-3-pro-preview", reasoning=ReasoningSettings()).chat(ASK)
    )
    shown = list(
        Gateway(adapter, model="gemini-3-pro-preview", reasoning=ReasoningSettings(show=True)).chat(
            ASK
        )
    )

    done = hidden[-1]
    assert isinstance(done, Done) and done.usage is not None
    assert (done.usage.completion_tokens, done.usage.reasoning_tokens) == (122, 120)
    assert not any(isinstance(e, ReasoningDelta) for e in hidden)
    assert ReasoningDelta("Rayleigh.") in shown and TextDelta("Blue.") in shown
    assert "generationConfig" not in transport.requests[0].json  # no effort: the default
    assert transport.requests[1].json["generationConfig"] == {
        "thinkingConfig": {"includeThoughts": True}
    }


# -- Bedrock -----------------------------------------------------------------------------

CONVERSE_REPLY = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "Rayleigh.", "signature": "s"}}},
                {"text": "Blue."},
            ],
        }
    },
    "stopReason": "end_turn",
    "usage": {"inputTokens": 12, "outputTokens": 40, "totalTokens": 52},
}


def _bedrock(*responses: HttpResponse) -> tuple[BedrockAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = BedrockAdapter(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key="AKID:SECRET",
        timeout_seconds=9,
        transport=transport,
    )
    return adapter, transport


def test_bedrock_claude_adaptive_model_gets_thinking_and_effort_in_additional_fields():
    adapter, transport = _bedrock(_json(CONVERSE_REPLY))

    events = list(
        adapter.chat(
            "global.anthropic.claude-opus-4-7",
            ASK,
            [],
            False,
            None,
            reasoning=ReasoningSettings(effort="high"),
        )
    )

    body = transport.requests[0].json
    assert body["additionalModelRequestFields"] == {
        "thinking": {"type": "adaptive", "block_binding": BINDING},
        "output_config": {"effort": "high"},
        "anthropic_beta": ["thinking-binding-controls-2026-08-01"],
    }
    assert body["inferenceConfig"]["maxTokens"] > 8192
    assert events[0] == ReasoningDelta("Rayleigh.") and events[1] == TextDelta("Blue.")


def test_bedrock_older_claude_model_gets_a_budget():
    adapter, transport = _bedrock(_json(CONVERSE_REPLY))

    list(
        adapter.chat(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            ASK,
            [],
            False,
            None,
            reasoning=ReasoningSettings(effort="low"),
        )
    )

    body = transport.requests[0].json
    assert body["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 1024, "block_binding": BINDING},
        "anthropic_beta": ["thinking-binding-controls-2026-08-01"],
    }


def test_bedrock_non_claude_model_is_unchanged():
    adapter, transport = _bedrock(_json(CONVERSE_REPLY), _json(CONVERSE_REPLY))

    list(adapter.chat("amazon.nova-pro-v1:0", ASK, [], False, None))
    list(
        adapter.chat(
            "amazon.nova-pro-v1:0", ASK, [], False, None, reasoning=ReasoningSettings(effort="high")
        )
    )

    assert transport.requests[0].json == transport.requests[1].json
    assert "additionalModelRequestFields" not in transport.requests[1].json


def test_bedrock_streams_reasoning_deltas():
    def frame(kind: str, payload: dict[str, Any]) -> bytes:
        return eventstream_frame(kind, payload)

    raw = b"".join(
        [
            frame("messageStart", {"role": "assistant"}),
            frame(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "Rayleigh."}}},
            ),
            frame(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"reasoningContent": {"signature": "s"}}},
            ),
            frame("contentBlockStop", {"contentBlockIndex": 0}),
            frame("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"text": "Blue."}}),
            frame("contentBlockStop", {"contentBlockIndex": 1}),
            frame("messageStop", {"stopReason": "end_turn"}),
        ]
    )
    adapter, _ = _bedrock(HttpResponse(200, {}, io.BytesIO(raw)))

    events = list(
        adapter.chat(
            "anthropic.claude-haiku-4-5", ASK, [], True, None, reasoning=ReasoningSettings()
        )
    )

    assert events[:2] == [ReasoningDelta("Rayleigh."), TextDelta("Blue.")]


# -- OpenAI-compatible: <think> tags are split even with the box unticked ------------------


@pytest.mark.parametrize("stream", [False, True])
def test_think_tags_never_reach_the_answer_of_a_model_not_marked_as_reasoning(stream: bool):
    server = StubLlmServer()
    server.inline_think = True
    with server.running():
        adapter = OpenAICompatibleAdapter(
            base_url=server.base_url, api_key=None, timeout_seconds=10, transport=UrllibTransport()
        )
        events = list(Gateway(adapter, model="stub").chat(ASK, stream=stream))

    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "OK"
    assert not any(isinstance(e, ReasoningDelta) for e in events)


# -- "Test connection": what each provider says about reasoning ----------------------------


def test_anthropic_models_api_capabilities_say_whether_a_model_thinks():
    adapter, transport = _anthropic(_json(_anthropic_model(adaptive=True, enabled=False)))

    assert adapter.reasoning_support("corp-sonnet") is True
    assert transport.requests[0].url == "http://llm.test/v1/models/corp-sonnet"


def test_gemini_model_info_thinking_flag_says_whether_a_model_thinks():
    info = {
        "name": "models/gemini-3-pro-preview",
        "inputTokenLimit": 1048576,
        "outputTokenLimit": 65536,
        "supportedGenerationMethods": ["generateContent", "countTokens"],
    }
    adapter, transport = _gemini(_json({**info, "thinking": True}), _json(info))

    assert adapter.reasoning_support("gemini-3-pro-preview") is True
    assert adapter.reasoning_support("gemini-3-pro-preview") is None
    assert transport.requests[0].url == "https://g.test/v1beta/models/gemini-3-pro-preview"


def _openai(*responses: HttpResponse) -> tuple[OpenAICompatibleAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = OpenAICompatibleAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


def test_ollama_show_capabilities_say_whether_a_model_thinks():
    show = {
        "modelfile": "FROM qwen3",
        "parameters": "num_ctx 32768",
        "details": {"family": "qwen3"},
        "model_info": {"general.architecture": "qwen3"},
        "capabilities": ["completion", "tools", "thinking"],
    }
    adapter, transport = _openai(_json(show))

    assert adapter.reasoning_support("qwen3") is True
    assert transport.requests[0].url == "http://llm.test/api/show"


def test_openrouter_supported_parameters_say_whether_a_model_reasons():
    models = {
        "data": [
            {
                "id": "deepseek/deepseek-r1",
                "context_length": 163840,
                "supported_parameters": ["max_tokens", "reasoning", "include_reasoning"],
            },
            {"id": "openai/gpt-4o", "supported_parameters": ["max_tokens", "tools"]},
        ]
    }
    not_ollama = {"error": "not found"}
    adapter, _ = _openai(
        _json(not_ollama, 404), _json(models), _json(not_ollama, 404), _json(models)
    )

    assert adapter.reasoning_support("deepseek/deepseek-r1") is True
    assert adapter.reasoning_support("openai/gpt-4o") is False


# -- "Test connection": the reply shows reasoning, before the gateway hides it --------------


def _shows_reasoning(adapter: Any, model: str) -> bool:
    events = list(Gateway(adapter, model=model).chat(ASK, keep_reasoning=True))
    done = events[-1]
    if any(isinstance(e, ReasoningDelta) for e in events):
        return True
    return isinstance(done, Done) and done.usage is not None and done.usage.reasoning_tokens > 0


def test_a_reply_with_reasoning_tokens_shows_reasoning():
    adapter, _ = _openai(
        _json(
            {
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 80,
                    "completion_tokens_details": {"reasoning_tokens": 64},
                },
            }
        )
    )
    assert _shows_reasoning(adapter, "o4-mini")


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
def test_a_reply_with_a_reasoning_field_shows_reasoning(field: str):
    message = {"role": "assistant", "content": "OK", field: "The user wants OK."}
    adapter, _ = _openai(_json({"choices": [{"message": message, "finish_reason": "stop"}]}))
    assert _shows_reasoning(adapter, "deepseek-reasoner")


def test_a_reply_with_a_think_block_shows_reasoning():
    message = {"role": "assistant", "content": "<think>They want OK.</think>OK"}
    adapter, _ = _openai(_json({"choices": [{"message": message, "finish_reason": "stop"}]}))
    assert _shows_reasoning(adapter, "qwen3")


def test_a_gemini_reply_with_thought_tokens_shows_reasoning():
    adapter, _ = _gemini(_json(GEMINI_REPLY))
    assert _shows_reasoning(adapter, "gemini-3-pro-preview")


def test_a_plain_reply_shows_no_reasoning():
    adapter, _ = _openai(
        _json({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})
    )
    assert not _shows_reasoning(adapter, "gpt-4o")
