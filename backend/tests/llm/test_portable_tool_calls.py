"""Tool-call ids and Gemini thought signatures survive a change of provider (ticket #550):
when a fallback takes over, the history one provider wrote is sent to another."""

from __future__ import annotations

import json
import re

import pytest

from dawam.modules.llm import Message, ToolCall, ToolCallEvent
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import PLACEHOLDER_SIGNATURE, GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.wire import received_call_id, sent_call_id
from tests.llm.test_gemini_and_bedrock import NOW, Queue, reply

PORTABLE = re.compile(r"\A[A-Za-z0-9_-]+\Z")
DONE_ANTHROPIC = {
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
DONE_BEDROCK = {
    "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 1, "outputTokens": 1},
}
DONE_GEMINI = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}


def openai_call(call_id: str) -> ToolCall:
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    adapter = OpenAICompatibleAdapter(
        base_url="http://o.test/v1",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"o.test": [reply(200, body)]}),
    )
    events = adapter.chat("m", [Message("user", "Go")], [], False, None)
    [call] = [e.call for e in events if isinstance(e, ToolCallEvent)]
    return call


def history(call: ToolCall) -> list[Message]:
    return [
        Message("user", "Go"),
        Message("assistant", None, (call,)),
        Message("tool", "found", tool_call_id=call.id),
    ]


def anthropic_body(messages: list[Message]) -> dict:
    queue = Queue(**{"a.test": [reply(200, DONE_ANTHROPIC)]})
    adapter = AnthropicAdapter(
        base_url="http://a.test/v1", api_key="k", timeout_seconds=9, transport=queue
    )
    list(adapter.chat("m", messages, [], False, None))
    return json.loads(queue.calls[0][2])


def bedrock_body(messages: list[Message]) -> dict:
    host = "bedrock-runtime.us-east-1.amazonaws.com"
    queue = Queue(**{host: [reply(200, DONE_BEDROCK)]})
    adapter = BedrockAdapter(
        base_url=f"https://{host}",
        api_key="AK:SK",
        timeout_seconds=9,
        transport=queue,
        now=lambda: NOW,
    )
    list(adapter.chat("m", messages, [], False, None))
    return json.loads(queue.calls[0][2])


def gemini_parts(messages: list[Message]) -> list[dict]:
    queue = Queue(**{"g.test": [reply(200, DONE_GEMINI)]})
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key="k", timeout_seconds=9, transport=queue
    )
    list(adapter.chat("m", messages, [], False, None))
    contents = json.loads(queue.calls[0][2])["contents"]
    return [p for c in contents for p in c["parts"] if "functionCall" in p]


@pytest.mark.parametrize("raw", ["", "call.1:x/y", "call_abc"])
def test_a_received_tool_call_id_is_portable(raw):
    call = openai_call(raw)

    assert PORTABLE.match(call.id)
    if raw == "call_abc":
        assert call.id == raw


def test_a_history_with_an_empty_openai_id_is_accepted_by_anthropic():
    body = anthropic_body(history(openai_call("")))

    [use] = [b for m in body["messages"] for b in m["content"] if b["type"] == "tool_use"]
    [result] = [b for m in body["messages"] for b in m["content"] if b["type"] == "tool_result"]
    assert PORTABLE.match(use["id"]) and result["tool_use_id"] == use["id"]


def test_a_history_with_an_empty_openai_id_is_accepted_by_bedrock():
    body = bedrock_body(history(openai_call("")))

    blocks = [b for m in body["messages"] for b in m["content"]]
    [use] = [b["toolUse"] for b in blocks if "toolUse" in b]
    [result] = [b["toolResult"] for b in blocks if "toolResult" in b]
    assert PORTABLE.match(use["toolUseId"]) and result["toolUseId"] == use["toolUseId"]


def test_an_id_saved_before_ids_were_portable_still_pairs_with_its_result():
    body = anthropic_body(history(ToolCall("", "lookup", {})))

    blocks = [b for m in body["messages"] for b in m["content"]]
    ids = {b.get("id") or b.get("tool_use_id") for b in blocks if b["type"] != "text"}
    assert len(ids) == 1 and PORTABLE.match(ids.pop())


def test_gemini_keeps_the_thought_signature_of_its_own_call_and_sends_it_back():
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {"name": "lookup", "args": {}},
                            "thoughtSignature": "sig-1",
                        },
                        {"functionCall": {"name": "lookup", "args": {"id": 2}}},
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    adapter = GeminiAdapter(
        base_url="https://g.test",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"g.test": [reply(200, body)]}),
    )
    first, second = [
        e.call
        for e in adapter.chat("m", [Message("user", "Go")], [], False, None)
        if isinstance(e, ToolCallEvent)
    ]

    assert first.thought_signature == "sig-1" and second.thought_signature == ""
    parts = gemini_parts([Message("user", "Go"), Message("assistant", None, (first, second))])
    assert parts[0]["thoughtSignature"] == "sig-1"
    assert "thoughtSignature" not in parts[1]  # Gemini signs only the first parallel call


def test_gemini_gets_the_placeholder_for_another_models_call():
    [part] = gemini_parts(history(openai_call("call_abc"))[:2])

    assert part["thoughtSignature"] == PLACEHOLDER_SIGNATURE


def test_only_gemini_is_sent_a_thought_signature():
    signed = ToolCall("c1", "lookup", {}, thought_signature="sig-1")

    assert "sig-1" not in json.dumps(anthropic_body(history(signed)))
    assert "sig-1" not in json.dumps(bedrock_body(history(signed)))


@pytest.mark.parametrize(
    ("one", "other"), [("a.b", "a:b"), ("x" * 64 + "1", "x" * 64 + "2"), ("a.b", "a_b")]
)
def test_ids_that_had_to_change_stay_distinct(one, other):
    assert sent_call_id(one) != sent_call_id(other)
    assert received_call_id(one) != received_call_id(other)
    for call_id in (one, other):
        sent = sent_call_id(call_id)
        assert PORTABLE.match(sent) and len(sent) <= 64
        assert sent_call_id(sent) == sent  # sent again unchanged
        assert sent_call_id(call_id) == sent  # stable


def test_a_portable_id_is_sent_as_it_is():
    assert sent_call_id("call_abc-1") == "call_abc-1"


def legacy_history() -> list[Message]:
    """Two calls saved with empty ids (before ids were portable), then their results."""
    calls = (ToolCall("", "lookup", {}), ToolCall("", "lookup", {"id": 2}))
    return [
        Message("user", "Go"),
        Message("assistant", None, calls),
        Message("tool", "first", tool_call_id=""),
        Message("tool", "second", tool_call_id=""),
    ]


def test_two_empty_ids_in_one_history_stay_distinct_for_anthropic():
    body = anthropic_body(legacy_history())

    blocks = [b for m in body["messages"] for b in m["content"]]
    uses = [b["id"] for b in blocks if b["type"] == "tool_use"]
    results = [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"]
    assert len(set(uses)) == 2 and results == uses
    assert all(PORTABLE.match(i) for i in uses)
    assert anthropic_body(legacy_history()) == body  # the same on every send


def test_two_empty_ids_in_one_history_stay_distinct_for_bedrock():
    body = bedrock_body(legacy_history())

    blocks = [b for m in body["messages"] for b in m["content"]]
    uses = [b["toolUse"]["toolUseId"] for b in blocks if "toolUse" in b]
    results = [b["toolResult"]["toolUseId"] for b in blocks if "toolResult" in b]
    assert len(set(uses)) == 2 and results == uses
    assert bedrock_body(legacy_history()) == body
