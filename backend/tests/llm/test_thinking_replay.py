"""Claude's thinking blocks go back unchanged between tool steps (#557): the Anthropic and
Bedrock adapters capture them (streamed and not, ``redacted_thinking`` included), replay them
first in their assistant message, ask for thinking on every step and send the
``block_binding`` safety net with its beta.

The replies below are built strictly from the documented formats (Anthropic Messages API
"preserved thinking" and streaming events, the thinking-binding-controls beta, Bedrock
Converse ``reasoningContent``): no API key was available to record them. Re-record with a
real key.
"""

from __future__ import annotations

import io
import json
from typing import Any

from dawam.modules.llm import Done, Message, ReasoningSettings, ToolCall
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import HttpResponse
from tests.llm.replay import ReplayTransport, eventstream_frame

BETA = "thinking-binding-controls-2026-08-01"
BINDING = {"prefix_mismatch_behavior": "drop_block"}
THOUGHT = {"type": "thinking", "thinking": "Look it up first.", "signature": "EqQBCgIYAhIM1"}
REDACTED = {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix"}
ASK = [Message("user", "Where is order 7?")]
CALL = ToolCall("toolu_01", "lookup", {"id": 7})
TRANSFORMED = {
    "type": "thinking_dropped",
    "path": "messages.1.content.0",
    "reason": "prefix_binding_mismatch",
}


def _json(body: Any) -> HttpResponse:
    return HttpResponse(200, {}, io.BytesIO(json.dumps(body).encode()))


def _sse(*events: dict[str, Any]) -> HttpResponse:
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return HttpResponse(200, {}, io.BytesIO("\n".join(lines).encode()))


def _anthropic(*responses: HttpResponse) -> tuple[AnthropicAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = AnthropicAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


TOOL_REPLY = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "content": [
        THOUGHT,
        REDACTED,
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "toolu_01", "name": "lookup", "input": {"id": 7}},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 30, "output_tokens": 60},
    "input_transformations": [TRANSFORMED],
}


def _done(events: list[Any]) -> Done:
    done = events[-1]
    assert isinstance(done, Done)
    return done


def test_a_reply_hands_over_its_thinking_blocks_unchanged_with_the_transformations():
    adapter, _ = _anthropic(_json(TOOL_REPLY))

    done = _done(list(adapter.chat("claude-opus-5-5", ASK, [], False, None, ReasoningSettings())))

    assert done.thinking == (THOUGHT, REDACTED)
    assert done.input_transformations == (TRANSFORMED,)


def test_a_streamed_reply_collects_thinking_with_its_signature_and_redacted_blocks():
    adapter, _ = _anthropic(
        _sse(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "usage": {"input_tokens": 30, "output_tokens": 1},
                    "input_transformations": [TRANSFORMED],
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Look it "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "up first."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "EqQBCgIYAhIM1"},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": REDACTED},
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "toolu_01", "name": "lookup"},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"id": 7}'},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 60},
            },
            {"type": "message_stop"},
        )
    )

    done = _done(list(adapter.chat("claude-opus-5-5", ASK, [], True, None, ReasoningSettings())))

    assert done.thinking == (THOUGHT, REDACTED)
    assert done.input_transformations == (TRANSFORMED,)


def test_replayed_blocks_go_first_and_unchanged_and_thinking_stays_on_after_a_tool_call():
    history = [
        *ASK,
        Message("assistant", "Checking.", (CALL,), thinking=(THOUGHT, REDACTED)),
        Message("tool", "row 7", tool_call_id="toolu_01"),
    ]
    adapter, transport = _anthropic(_json(TOOL_REPLY))

    list(adapter.chat("claude-opus-5-5", history, [], False, None, ReasoningSettings("low")))

    sent = transport.requests[0]
    assistant = sent.json["messages"][1]
    assert assistant["content"][:2] == [THOUGHT, REDACTED]
    assert [b["type"] for b in assistant["content"][2:]] == ["text", "tool_use"]
    assert sent.json["thinking"] == {"type": "adaptive", "block_binding": BINDING}
    assert sent.json["output_config"] == {"effort": "low"}
    assert sent.headers["anthropic-beta"] == BETA


def test_an_older_claude_gets_the_binding_beside_its_budget():
    adapter, transport = _anthropic(_json(TOOL_REPLY))

    list(adapter.chat("claude-haiku-4-5", ASK, [], False, None, ReasoningSettings("low")))

    assert transport.requests[0].json["thinking"] == {
        "type": "enabled",
        "budget_tokens": 1024,
        "block_binding": BINDING,
    }


def test_without_reasoning_settings_there_is_no_binding_and_no_beta():
    adapter, transport = _anthropic(_json(TOOL_REPLY))

    list(adapter.chat("claude-opus-5-5", ASK, [], False, None))

    sent = transport.requests[0]
    assert "thinking" not in sent.json and "anthropic-beta" not in sent.headers


def test_blocks_another_provider_wrote_are_not_sent_to_anthropic():
    bedrock_block = {"reasoningContent": {"reasoningText": {"text": "x", "signature": "s"}}}
    history = [*ASK, Message("assistant", None, (CALL,), thinking=(bedrock_block,))]
    history.append(Message("tool", "row 7", tool_call_id="toolu_01"))
    adapter, transport = _anthropic(_json(TOOL_REPLY))

    list(adapter.chat("claude-opus-5-5", history, [], False, None, ReasoningSettings()))

    assert [b["type"] for b in transport.requests[0].json["messages"][1]["content"]] == ["tool_use"]


# -- Bedrock (Converse) ------------------------------------------------------------------

REASONED = {"reasoningContent": {"reasoningText": {"text": "Look it up.", "signature": "sig1"}}}
BEDROCK_REDACTED = {"reasoningContent": {"redactedContent": "cmVkYWN0ZWQ="}}
CONVERSE_TOOL_REPLY = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                REASONED,
                BEDROCK_REDACTED,
                {"text": "Checking."},
                {"toolUse": {"toolUseId": "toolu_01", "name": "lookup", "input": {"id": 7}}},
            ],
        }
    },
    "stopReason": "tool_use",
    "usage": {"inputTokens": 30, "outputTokens": 60, "totalTokens": 90},
    "additionalModelResponseFields": {"input_transformations": [TRANSFORMED]},
}


def _bedrock(*responses: HttpResponse) -> tuple[BedrockAdapter, ReplayTransport]:
    transport = ReplayTransport(*responses)
    adapter = BedrockAdapter(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key="AKIDEXAMPLE:secret",
        timeout_seconds=9,
        transport=transport,
    )
    return adapter, transport


MODEL = "us.anthropic.claude-opus-4-7-v1"


def test_bedrock_claude_hands_over_its_reasoning_blocks_and_transformations():
    adapter, _ = _bedrock(_json(CONVERSE_TOOL_REPLY))

    done = _done(list(adapter.chat(MODEL, ASK, [], False, None, ReasoningSettings())))

    assert done.thinking == (REASONED, BEDROCK_REDACTED)
    assert done.input_transformations == (TRANSFORMED,)


def test_bedrock_streamed_reasoning_is_collected_with_its_signature():
    raw = b"".join(
        [
            eventstream_frame("messageStart", {"role": "assistant"}),
            eventstream_frame(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "Look "}}},
            ),
            eventstream_frame(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "it up."}}},
            ),
            eventstream_frame(
                "contentBlockDelta",
                {"contentBlockIndex": 0, "delta": {"reasoningContent": {"signature": "sig1"}}},
            ),
            eventstream_frame("contentBlockStop", {"contentBlockIndex": 0}),
            eventstream_frame(
                "contentBlockDelta",
                {
                    "contentBlockIndex": 1,
                    "delta": {"reasoningContent": {"redactedContent": "cmVkYWN0ZWQ="}},
                },
            ),
            eventstream_frame("contentBlockStop", {"contentBlockIndex": 1}),
            eventstream_frame(
                "contentBlockDelta", {"contentBlockIndex": 2, "delta": {"text": "Hi"}}
            ),
            eventstream_frame("contentBlockStop", {"contentBlockIndex": 2}),
            eventstream_frame(
                "messageStop",
                {
                    "stopReason": "end_turn",
                    "additionalModelResponseFields": {"input_transformations": [TRANSFORMED]},
                },
            ),
        ]
    )
    adapter, _ = _bedrock(HttpResponse(200, {}, io.BytesIO(raw)))

    done = _done(list(adapter.chat(MODEL, ASK, [], True, None, ReasoningSettings())))

    assert done.thinking == (REASONED, BEDROCK_REDACTED)
    assert done.input_transformations == (TRANSFORMED,)


def test_bedrock_replays_the_blocks_first_and_sends_the_binding_with_its_beta():
    history = [
        *ASK,
        Message("assistant", "Checking.", (CALL,), thinking=(REASONED, BEDROCK_REDACTED, THOUGHT)),
        Message("tool", "row 7", tool_call_id="toolu_01"),
    ]
    adapter, transport = _bedrock(_json(CONVERSE_TOOL_REPLY))

    list(adapter.chat(MODEL, history, [], False, None, ReasoningSettings("high")))

    body = transport.requests[0].json
    assistant = body["messages"][1]["content"]
    assert assistant[:2] == [REASONED, BEDROCK_REDACTED]  # the Anthropic-API block is left out
    assert "text" in assistant[2] and "toolUse" in assistant[3]
    assert body["additionalModelRequestFields"] == {
        "thinking": {"type": "adaptive", "block_binding": BINDING},
        "output_config": {"effort": "high"},
        "anthropic_beta": [BETA],
    }
    assert body["additionalModelResponseFieldPaths"] == ["/input_transformations"]


# -- other providers ignore the blocks -----------------------------------------------------


def test_other_adapters_ignore_message_thinking():
    history = [
        *ASK,
        Message("assistant", "Checking.", (CALL,), thinking=(THOUGHT, REASONED)),
        Message("tool", "row 7", tool_call_id="toolu_01"),
    ]
    plain = [*ASK, Message("assistant", "Checking.", (CALL,)), history[-1]]
    openai_reply = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
    }
    gemini_reply = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    for make, reply, model in (
        (OpenAICompatibleAdapter, openai_reply, "gpt-4o"),
        (GeminiAdapter, gemini_reply, "gemini-2.5-pro"),
        (BedrockAdapter, CONVERSE_TOOL_REPLY, "amazon.nova-pro-v1:0"),
    ):
        sent = []
        for messages in (history, plain):
            transport = ReplayTransport(_json(reply))
            adapter = make(
                base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
                api_key="AKIDEXAMPLE:secret",
                timeout_seconds=9,
                transport=transport,
            )
            list(adapter.chat(model, messages, [], False, None))
            sent.append(transport.requests[0].json)
        assert sent[0] == sent[1], make.__name__
