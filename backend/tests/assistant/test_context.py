"""Context management: large tool results and old turns are trimmed to fit the window."""

from __future__ import annotations

from dawam.modules.assistant.internal.context import (
    estimate_tokens,
    fit_messages,
    shorten_result,
)
from dawam.modules.llm import Message, ToolCall


def total(messages) -> int:
    return sum(estimate_tokens(m.content or "") for m in messages)


def test_a_small_result_is_left_alone_and_a_large_one_is_cut_with_a_note():
    assert shorten_result("short", 1000) == "short"
    assert shorten_result("x" * 100_000, None) == "x" * 100_000

    cut = shorten_result("x" * 100_000, 1000)

    assert len(cut) < 100_000 // 4 and cut.startswith("xxx") and "left out" in cut


def test_without_a_known_window_nothing_is_trimmed():
    messages = [Message("system", "s"), *[Message("user", "y" * 4000) for _ in range(10)]]
    assert fit_messages(messages, None, protect_from=10) == messages


def test_oldest_turns_go_first_and_the_member_message_stays():
    history = [
        Message("user" if i % 2 == 0 else "assistant", f"{i} " + "w" * 1000) for i in range(8)
    ]
    history.append(Message("user", "LAST QUESTION"))
    messages = [Message("system", "SYS"), *history]

    fitted = fit_messages(messages, 1000, protect_from=len(history))

    assert fitted[0].content is not None and fitted[0].content.startswith("SYS")
    assert "left out" in fitted[0].content
    assert fitted[-1].content == "LAST QUESTION"
    assert total(fitted) <= 1000
    kept = [m.content for m in fitted[1:-1]]
    assert kept == [m.content for m in history[8 - len(kept) : 8]]  # a newest-first suffix
    assert not kept or fitted[1].role == "user"  # never starts on an assistant turn
    assert messages[1:] == history  # the input is not changed


def test_a_tool_call_and_its_results_are_dropped_together():
    call = ToolCall("c1", "t", {})
    messages = [
        Message("system", "SYS"),
        Message("assistant", "a" * 4000, (call,)),
        Message("tool", "r" * 4000, tool_call_id="c1"),
        Message("user", "now"),
    ]

    fitted = fit_messages(messages, 600, protect_from=3)

    assert [m.role for m in fitted] == ["system", "user"]


def test_everything_fits_means_no_change_and_no_note():
    messages = [Message("system", "SYS"), Message("user", "hi")]
    assert fit_messages(messages, 8000, protect_from=1) == messages


def test_old_tool_results_in_the_current_run_are_replaced_when_still_too_big():
    result = '<data source="tool:lookup">\n' + "r" * 3000 + "\n</data>"
    messages = [
        Message("system", "SYS"),
        Message("user", "q"),
        Message("assistant", None, (ToolCall("c1", "lookup", {}),)),
        Message("tool", result, tool_call_id="c1"),
        Message("assistant", None, (ToolCall("c2", "lookup", {}),)),
        Message("tool", result, tool_call_id="c2"),
    ]

    fitted = fit_messages(messages, 1500, protect_from=1)

    assert "left out" in (fitted[3].content or "") and fitted[5].content == result
    assert (fitted[3].content or "").startswith('<data source="tool:lookup">')
