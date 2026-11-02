"""The agent loop when a fallback model takes over (ticket #550): the step is fitted again
to the new model. Two scripted fake providers behind ``MeteredGateway``s; no database."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Sequence
from typing import Any

from dawam.modules.assistant.internal.agent import Finished, ToolOutcome, run_agent
from dawam.modules.assistant.internal.context import prompt_budget, request_tokens
from dawam.modules.llm import (
    Capabilities,
    FakeAdapter,
    Gateway,
    LlmError,
    Message,
    Reply,
    ToolCall,
    ToolSpec,
)
from dawam.modules.llm.gateway import AUTH, CONTEXT_OVERFLOW
from dawam.modules.llm.role_service import MeteredGateway

LOOKUP = ToolSpec(
    "lookup", "Find it.", {"type": "object", "properties": {"id": {"type": "integer"}}}
)


class Tools:
    def specs(self) -> Sequence[ToolSpec]:
        return [LOOKUP]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome("found it " * 40, "ok")


class Budgets:
    """Stands in for the ``RoleService``: every call is within budget, usage is dropped."""

    def ensure_within_budget(self, workspace_id: Any) -> None:
        return None

    def record_usage(self, usage: Any, **_: Any) -> None:
        return None


def metered(name: str, adapter: FakeAdapter, capabilities: Capabilities, **options: Any) -> Any:
    gateway = Gateway(adapter, model=name, capabilities=capabilities, sleep=lambda _: None)
    return MeteredGateway(
        gateway,
        Budgets(),  # type: ignore[arg-type]
        role="agent",
        model_name=name,
        model_id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        workspace_id=None,
        user_id=None,
        **options,
    )


def chain(primary: FakeAdapter, backup: FakeAdapter, backup_caps: Capabilities) -> Any:
    fallback = metered("backup", backup, backup_caps)
    full = Capabilities(tool_calling=True, streaming=True, context_window=100_000)
    return metered("primary", primary, full, fallbacks=[fallback])


def run(gateway: Any, history: Sequence[Message]) -> list[Any]:
    clock = itertools.count(0.0, 0.5)
    return list(
        run_agent(gateway, Tools(), system="SYSTEM", history=history, timer=lambda: next(clock))
    )


def test_a_fallback_with_a_smaller_window_gets_a_history_that_fits():
    primary = FakeAdapter().script(LlmError(AUTH, "bad key"))
    backup = FakeAdapter().script(Reply(text="from backup"))
    small = Capabilities(tool_calling=True, streaming=True, context_window=400)
    old_turns = [Message("user" if i % 2 == 0 else "assistant", "old " * 120) for i in range(8)]
    gateway = chain(primary, backup, small)

    events = run(gateway, [*old_turns, Message("user", "Where is it?")])

    assert events[-1].status == "completed" and events[-1].text == "from backup"
    sent = backup.calls[0].messages
    budget = prompt_budget(400)
    assert budget is not None and request_tokens(sent) <= budget
    assert sent[-1].content == "Where is it?"
    assert gateway.fell_back and gateway.model_name == "backup"


def test_a_fallback_without_native_tool_calling_runs_in_prompted_mode():
    primary = FakeAdapter().script(
        Reply(tool_calls=(ToolCall("c1", "lookup", {"id": 7}),)), LlmError(AUTH, "bad key")
    )
    backup = FakeAdapter().script(Reply(text="It is at row 7"))
    no_tools = Capabilities(tool_calling=False, streaming=True, context_window=100_000)

    events = run(chain(primary, backup, no_tools), [Message("user", "Where is it?")])

    assert isinstance(events[-1], Finished) and events[-1].status == "completed"
    asked = backup.calls[0]
    assert asked.tools == ()
    assert "You have these tools" in (asked.messages[0].content or "")
    assert all(m.role != "tool" and not m.tool_calls for m in asked.messages)
    assert '"tool": "lookup"' in (asked.messages[2].content or "")


def test_an_overflow_is_shortened_first_then_handed_over():
    too_long = LlmError(CONTEXT_OVERFLOW, "too long")
    primary = FakeAdapter().script(
        Reply(tool_calls=(ToolCall("c1", "lookup", {}),)), too_long, too_long
    )
    backup = FakeAdapter().script(Reply(text="done"))
    full = Capabilities(tool_calling=True, streaming=True, context_window=100_000)

    events = run(chain(primary, backup, full), [Message("user", "Where is it?")])

    assert events[-1].status == "completed" and events[-1].text == "done"
    assert len(primary.calls) == 3  # the step, then once more with the results as digests
    assert "[shortened" in (backup.calls[0].messages[-1].content or "")


def test_a_single_model_failing_still_fails_the_run():
    gateway = metered(
        "only", FakeAdapter().script(LlmError(AUTH, "bad key")), Capabilities(streaming=True)
    )

    events = run(gateway, [Message("user", "Where is it?")])

    assert events[-1].status == "failed" and events[-1].error_code == AUTH


def test_a_handover_outside_the_loop_is_adopted_at_the_next_step():
    primary = FakeAdapter().script(
        Reply(tool_calls=(ToolCall("c1", "lookup", {"id": 7}),)), LlmError(AUTH, "bad key")
    )
    backup = FakeAdapter().script(Reply(text="checked"), Reply(text="It is at row 7"))
    no_tools = Capabilities(tool_calling=False, streaming=True, context_window=100_000)
    gateway = chain(primary, backup, no_tools)

    class Checking(Tools):
        """A tool whose result is checked on the run's gateway (like the light model's
        injection check): the primary fails there and the backup takes over."""

        def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
            list(gateway.chat([Message("user", "Is this safe?")]))
            return super().execute(name, arguments)

    clock = itertools.count(0.0, 0.5)
    events = list(
        run_agent(
            gateway,
            Checking(),
            system="SYSTEM",
            history=[Message("user", "Where is it?")],
            timer=lambda: next(clock),
        )
    )

    assert events[-1].status == "completed" and events[-1].text == "It is at row 7"
    step = backup.calls[1]
    assert step.tools == () and "You have these tools" in (step.messages[0].content or "")
    assert all(m.role != "tool" and not m.tool_calls for m in step.messages)
