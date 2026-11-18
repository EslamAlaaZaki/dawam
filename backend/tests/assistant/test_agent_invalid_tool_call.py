"""The agent loop when the provider rejects the model's tool call (ticket #615): the model is
told why and asked again; two rejections in a row fail the run."""

from __future__ import annotations

import itertools

from dawam.modules.assistant.internal.agent import Finished, run_agent
from dawam.modules.llm import (
    FakeAdapter,
    Gateway,
    LlmError,
    Message,
    Reply,
    TextDelta,
    ToolCall,
)
from dawam.modules.llm.gateway import INVALID_TOOL_CALL
from tests.assistant.test_agent import Tools


def run(adapter: FakeAdapter) -> list:
    clock = itertools.count(0.0, 0.5)
    return list(
        run_agent(
            Gateway(adapter, model="m", sleep=lambda _: None),
            Tools(),
            system="SYSTEM",
            history=[Message("user", "Where is it?")],
            timer=lambda: next(clock),
        )
    )


def rejected(reason: str = "tool call validation failed") -> LlmError:
    return LlmError(INVALID_TOOL_CALL, reason)


def test_a_rejected_tool_call_is_given_back_to_the_model_and_the_retry_succeeds():
    adapter = FakeAdapter().script(rejected(), Reply(text="Found it."))

    finished = run(adapter)[-1]

    assert isinstance(finished, Finished)
    assert finished.status == "completed" and finished.text == "Found it."
    note = adapter.calls[1].messages[-1]
    assert note.role == "user"
    assert note.content == (
        "Your last tool call was rejected: tool call validation failed. "
        "Call the tool again with arguments that match its schema."
    )


def test_a_retried_call_can_then_run_its_tool():
    adapter = FakeAdapter().script(
        rejected(),
        Reply(tool_calls=[ToolCall("c1", "lookup", {"id": 1})]),
        Reply(text="Done."),
    )

    finished = run(adapter)[-1]

    assert finished.status == "completed"
    assert [c.name for c in finished.tool_calls] == ["lookup"]


def test_two_rejections_in_a_row_fail_the_run():
    adapter = FakeAdapter().script(rejected(), rejected(), Reply(text="never asked"))

    finished = run(adapter)[-1]

    assert finished.status == "failed"
    assert finished.error_code == "invalid_tool_call"
    assert finished.error_message == (
        "The model made an invalid tool call. Sending the prompt again usually works."
    )
    assert len(adapter.calls) == 2


def test_rejections_that_are_not_in_a_row_do_not_add_up():
    adapter = FakeAdapter().script(
        rejected(),
        Reply(tool_calls=[ToolCall("c1", "lookup", {"id": 1})]),
        rejected(),
        Reply(text="Done."),
    )

    finished = run(adapter)[-1]

    assert finished.status == "completed"


def test_text_streamed_before_a_rejection_is_not_kept():
    class Partial(FakeAdapter):
        def chat(self, *args, **kwargs):
            if not self.calls:
                self.calls.append(None)
                yield TextDelta("half an ans")
                raise rejected()
            yield from super().chat(*args, **kwargs)

    adapter = Partial().script(Reply(text="Whole answer."))
    adapter.calls = []

    finished = run(adapter)[-1]

    assert finished.status == "completed"
    assert finished.text == "Whole answer."
