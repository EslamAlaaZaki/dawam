"""The tool-use loop (spec §6.16).

``run_agent`` depends only on the gateway interface (``ChatGateway``: a ``Gateway`` or a
``MeteredGateway``) and a ``ToolSet``; it knows no vendor, no database and no user. It
yields ``Text`` deltas and ``ToolFinished`` reports as they happen, then one ``Finished``
that says how the run ended: ``completed``, ``tool_limit`` (the cap was hit and the model
answered with what it had), ``cancelled`` (the member pressed stop) or ``failed`` (the
provider or a budget refused; the reason is kept).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from dawam.modules.llm import (
    Capabilities,
    ChatEvent,
    Done,
    LlmError,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from dawam.platform.errors import ApiError

from .prompt import quote_data

DEFAULT_MAX_TOOL_CALLS = 25

ToolStatus = Literal["ok", "refused", "error", "skipped"]
RunStatus = Literal["completed", "tool_limit", "cancelled", "failed"]

LIMIT_MESSAGE = (
    "The tool-call limit for this request is used up. Answer with what you have already found."
)


class ChatGateway(Protocol):
    def capabilities(self) -> Capabilities: ...

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        stream: bool = False,
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> Iterator[ChatEvent]: ...


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    status: ToolStatus


class ToolSet(Protocol):
    def specs(self) -> Sequence[ToolSpec]:
        """The tools to offer the model."""
        ...

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Run one tool call. Never raises for an ordinary failure: it says ``refused`` or
        ``error`` in the outcome."""
        ...


@dataclass(frozen=True)
class ToolCallRecord:
    id: str
    name: str
    arguments: dict[str, Any]
    status: ToolStatus
    duration_ms: int


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolFinished:
    call: ToolCallRecord


@dataclass(frozen=True)
class Finished:
    status: RunStatus
    text: str
    tool_calls: list[ToolCallRecord]
    prompt_tokens: int
    completion_tokens: int
    error_code: str | None = None
    error_message: str | None = None


AgentEvent = Text | ToolFinished | Finished


@dataclass
class _Run:
    text: list[str] = field(default_factory=list)
    calls: list[ToolCallRecord] = field(default_factory=list)
    prompt: int = 0
    completion: int = 0

    def finish(
        self, status: RunStatus, code: str | None = None, message: str | None = None
    ) -> Finished:
        return Finished(
            status, "".join(self.text), self.calls, self.prompt, self.completion, code, message
        )


def run_agent(
    gateway: ChatGateway,
    tools: ToolSet,
    *,
    system: str,
    history: Sequence[Message],
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    is_cancelled: Callable[[], bool] = lambda: False,
    timer: Callable[[], float] = time.monotonic,
    delta_poll_seconds: float = 0.0,
) -> Iterator[AgentEvent]:
    """Run the conversation ``history`` (ending with the member's message) to an answer."""
    run = _Run()
    messages = [Message("system", system), *history]
    capabilities = gateway.capabilities()
    stream = capabilities.streaming is not False
    tools_usable = capabilities.tool_calling is not False
    limit_hit = False
    known_tools = {spec.name for spec in tools.specs()}
    last_poll = timer()
    try:
        while True:
            if is_cancelled():
                yield run.finish("cancelled")
                return
            offered = tools.specs() if tools_usable and len(run.calls) < max_tool_calls else ()
            pending: list[ToolCall] = []
            answer: list[str] = []
            last_poll = float("-inf")  # always look at the flag when a model call begins
            with closing(gateway.chat(messages, offered, stream)) as events:
                for event in events:
                    # Reading the stop flag is a query: while text streams, not on every delta.
                    if timer() - last_poll >= delta_poll_seconds:
                        last_poll = timer()
                        if is_cancelled():
                            yield run.finish("cancelled")
                            return
                    if isinstance(event, TextDelta):
                        answer.append(event.text)
                        run.text.append(event.text)
                        yield Text(event.text)
                    elif isinstance(event, ToolCallEvent):
                        if offered:
                            pending.append(event.call)
                    elif isinstance(event, Done) and event.usage is not None:
                        run.prompt += event.usage.prompt_tokens
                        run.completion += event.usage.completion_tokens
            if not pending:
                yield run.finish("tool_limit" if limit_hit else "completed")
                return
            messages.append(Message("assistant", "".join(answer) or None, tuple(pending)))
            executed = sum(1 for c in run.calls if c.status != "skipped")
            for call in pending:
                if is_cancelled():
                    yield run.finish("cancelled")
                    return
                if executed >= max_tool_calls:
                    limit_hit = True
                    outcome, duration = ToolOutcome(LIMIT_MESSAGE, "skipped"), 0
                else:
                    started = timer()
                    outcome = tools.execute(call.name, call.arguments)
                    duration = round((timer() - started) * 1000)
                    executed += 1
                record = ToolCallRecord(
                    call.id, call.name, call.arguments, outcome.status, duration
                )
                run.calls.append(record)
                yield ToolFinished(record)
                messages.append(
                    Message(
                        "tool",
                        quote_data(
                            f"tool:{call.name if call.name in known_tools else 'unknown'}",
                            outcome.content,
                        ),
                        tool_call_id=call.id,
                    )
                )
    except LlmError as exc:
        yield run.finish("failed", exc.code, exc.message)
    except ApiError as exc:
        yield run.finish("failed", exc.code, exc.message)
