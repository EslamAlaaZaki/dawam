"""Claude's thinking blocks are carried between the steps of a run (#557): kept on the
assistant message that made the calls, saved with the checkpoint, and replayed unchanged even
where the conversation before them changed (compaction, a changed tool list, trimmed results):
the provider's ``block_binding`` drops what no longer matches, while a tool turn sent without
its blocks would be refused. The model is the scripted ``FakeAdapter`` through the real
``Gateway``."""

from __future__ import annotations

import json
import logging
from typing import Any

from dawam.modules.assistant.internal import agent as agent_module
from dawam.modules.assistant.internal.agent import Checkpoint, Compacted, run_agent
from dawam.modules.llm import (
    CONTEXT_OVERFLOW,
    Capabilities,
    FakeAdapter,
    LlmError,
    Message,
    Reply,
    ToolCall,
)
from tests.assistant.test_agent import ManyTools, Tools, call, checkpoints_of, gateway, run

FIRST = {"type": "thinking", "thinking": "Look it up.", "signature": "sig-1"}
SECOND = {"type": "redacted_thinking", "data": "opaque-2"}


def assistants(messages: tuple[Message, ...]) -> list[Message]:
    return [m for m in messages if m.role == "assistant"]


def test_each_steps_blocks_go_back_unchanged_on_the_message_that_made_the_calls():
    adapter = FakeAdapter().script(
        Reply(tool_calls=(call(1, id=1),), thinking=(FIRST,)),
        Reply(tool_calls=(call(2, id=2),), thinking=(SECOND,)),
        Reply(text="Found both"),
    )

    events = run(adapter)

    assert events[-1].status == "completed"
    second, third = adapter.calls[1].messages, adapter.calls[2].messages
    assert [m.thinking for m in assistants(second)] == [(FIRST,)]
    assert [m.thinking for m in assistants(third)] == [(FIRST,), (SECOND,)]
    assert assistants(third)[0].tool_calls == (call(1, id=1),)


def test_a_resumed_run_sends_the_same_blocks_as_before_the_pause():
    first = FakeAdapter().script(
        Reply(tool_calls=(call(1, id=1),), thinking=(FIRST, SECOND)), Reply(text="x")
    )
    saved, _ = checkpoints_of(first)
    stored = Checkpoint.from_json(json.loads(json.dumps(saved[0].to_json())))
    assert stored is not None
    adapter = FakeAdapter().script(Reply(text="Found it"))

    run(adapter, resume=stored)

    assert first.calls[1].messages[2].thinking == (FIRST, SECOND)
    assert adapter.calls[0].messages[2].thinking == (FIRST, SECOND)


def test_an_older_checkpoint_without_blocks_still_resumes():
    first = FakeAdapter().script(Reply(tool_calls=(call(1, id=1),)), Reply(text="x"))
    saved, _ = checkpoints_of(first)
    data = saved[0].to_json()
    for message in data["messages"]:
        message.pop("thinking")

    stored = Checkpoint.from_json(data)

    assert stored is not None and all(m.thinking == () for m in stored.messages)


def test_compaction_removes_the_oldest_turns_and_the_kept_ones_keep_their_blocks():
    big = "lorem ipsum " * 660  # about 2,000 tokens
    history = [
        Message("user", big),
        Message("assistant", big),
        Message("user", "and then?"),
        Message("assistant", "Then this.", thinking=(FIRST,)),
        Message("user", "Where is it?"),
    ]
    adapter = FakeAdapter().script(Reply(text="Here"))

    events = list(
        run_with(adapter, history, summarize=lambda summary, turns: "THE SUMMARY", window=4000)
    )

    assert any(isinstance(e, Compacted) for e in events)
    sent = adapter.calls[0].messages
    assert big not in [m.content for m in sent]
    assert [m.thinking for m in assistants(sent)] == [(FIRST,)]


def run_with(adapter: FakeAdapter, history: list[Message], *, window: int, **options: Any):
    return run_agent(
        gateway(adapter, capabilities=Capabilities(context_window=window)),
        Tools(),
        system="SYSTEM",
        history=history,
        **options,
    )


def test_when_find_tools_changes_the_tools_the_earlier_blocks_still_go_back(monkeypatch):
    monkeypatch.setattr(agent_module, "TOOL_SEARCH_THRESHOLD", 30)
    find = ToolCall("f1", "find_tools", {"query": "revenue report"})
    use = ToolCall("u1", "zeta_report", {})
    adapter = FakeAdapter().script(
        Reply(tool_calls=(find,), thinking=(FIRST,)),
        Reply(tool_calls=(use,), thinking=(SECOND,)),
        Reply(text="done"),
    )

    run(adapter, ManyTools(), core_tools={"tool_00"})

    second, third = adapter.calls[1].messages, adapter.calls[2].messages
    assert [m.thinking for m in assistants(second)] == [(FIRST,)]  # the tool list grew
    assert [m.thinking for m in assistants(third)] == [(FIRST,), (SECOND,)]


def test_the_providers_input_transformations_reach_the_log(caplog):
    dropped = {"type": "thinking_dropped", "path": "messages.2.content.0", "reason": "x_y"}
    adapter = FakeAdapter().script(Reply(text="Here", input_transformations=(dropped,)))

    with caplog.at_level(logging.WARNING):
        run(adapter)

    [record] = [r for r in caplog.records if r.getMessage() == "model input transformed"]
    assert (record.transformation, record.reason, record.path) == (  # type: ignore[attr-defined]
        "thinking_dropped",
        "x_y",
        "messages.2.content.0",
    )


def test_a_result_shortened_to_fit_keeps_every_block():
    adapter = FakeAdapter().script(
        Reply(tool_calls=(call(1, id=1),), thinking=(FIRST,)),
        Reply(tool_calls=(call(2, id=2),), thinking=(SECOND,)),
        LlmError(CONTEXT_OVERFLOW, "too large"),
        Reply(text="Found both"),
    )

    events = run(adapter)

    assert events[-1].status == "completed"
    retried = adapter.calls[3].messages
    assert [m.thinking for m in assistants(retried)] == [(FIRST,), (SECOND,)]
