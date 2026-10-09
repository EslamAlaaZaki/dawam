"""The tool-use loop (spec §6.16).

``run_agent`` depends only on the gateway interface (``ChatGateway``: a ``Gateway`` or a
``MeteredGateway``) and a ``ToolSet``; it knows no vendor, no database and no user. It
yields ``Text`` deltas and ``ToolFinished`` reports as they happen, then one ``Finished``
that says how the run ended: ``completed``, ``tool_limit`` (the cap was hit and the model
answered with what it had), ``cancelled`` (the member pressed stop) or ``failed`` (the
provider or a budget refused; the reason is kept).

A model known to lack native tool calling runs in limited mode (``prompted.py``): the tools
are described in the system prompt and a JSON reply is parsed strictly. Before every model
call the conversation is fitted to the model's context window (``context.py``).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
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

from .context import fit_messages, shorten_result
from .prompt import quote_data
from .prompted import MAX_RETRIES, PromptedInvalid, correction, parse_reply, tools_prompt

DEFAULT_MAX_TOOL_CALLS = 25
DEFAULT_MAX_JOB_TOOL_CALLS = 150
"""The cap of a background job, which explores far more than a chat answer."""

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
    saved: dict[str, Any] | None = None
    """What of the result may be kept on the run (never row values); ``None``: nothing."""


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
    result: dict[str, Any] | None = None
    """``ToolOutcome.saved``."""


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
    capabilities = gateway.capabilities()
    window = capabilities.context_window
    stream = capabilities.streaming is not False
    prompted = capabilities.tool_calling is False  # limited mode: tools are described in text
    messages = [Message("system", system), *history]
    protect_from = len(history)  # the member's latest message
    limit_hit = False
    specs = {spec.name: spec for spec in tools.specs()}
    known_tools = set(specs)
    retries = 0
    last_poll = timer()
    try:
        while True:
            if is_cancelled():
                yield run.finish("cancelled")
                return
            under_cap = len(run.calls) < max_tool_calls
            offered = tools.specs() if under_cap else ()
            pending: list[ToolCall] = []
            answer: list[str] = []
            last_poll = float("-inf")  # always look at the flag when a model call begins
            sent = fit_messages(messages, window, protect_from=protect_from)
            if prompted and offered:
                head = sent[0]
                sent[0] = replace(head, content=head.content + "\n\n" + tools_prompt(offered))
            with closing(gateway.chat(sent, () if prompted else offered, stream)) as events:
                for event in events:
                    # Reading the stop flag is a query: while text streams, not on every delta.
                    if timer() - last_poll >= delta_poll_seconds:
                        last_poll = timer()
                        if is_cancelled():
                            yield run.finish("cancelled")
                            return
                    if isinstance(event, TextDelta):
                        answer.append(event.text)
                        if not prompted:
                            run.text.append(event.text)
                            yield Text(event.text)
                    elif isinstance(event, ToolCallEvent):
                        if offered and not prompted:
                            pending.append(event.call)
                    elif isinstance(event, Done) and event.usage is not None:
                        run.prompt += event.usage.prompt_tokens
                        run.completion += event.usage.completion_tokens
            if prompted:
                reply = "".join(answer)
                verdict = parse_reply(reply, specs) if offered else None
                if isinstance(verdict, PromptedInvalid):
                    if retries >= MAX_RETRIES:
                        yield run.finish(
                            "failed",
                            "invalid_tool_call",
                            "The model could not produce a valid tool call.",
                        )
                        return
                    retries += 1
                    messages.append(Message("assistant", reply))
                    messages.append(Message("user", correction(verdict.reason)))
                    continue
                if verdict is None:
                    if reply:
                        run.text.append(reply)
                        yield Text(reply)
                else:
                    retries = 0
                    call_id = f"call_{len(run.calls) + 1}"
                    pending.append(ToolCall(call_id, verdict.name, verdict.arguments))
            if not pending:
                capped = limit_hit or (prompted and not under_cap)
                yield run.finish("tool_limit" if capped else "completed")
                return
            if prompted:
                messages.append(Message("assistant", "".join(answer)))
            else:
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
                    call.id, call.name, call.arguments, outcome.status, duration, outcome.saved
                )
                run.calls.append(record)
                yield ToolFinished(record)
                quoted = quote_data(
                    f"tool:{call.name if call.name in known_tools else 'unknown'}",
                    shorten_result(outcome.content, window),
                )
                if prompted:
                    messages.append(Message("user", quoted))
                else:
                    messages.append(Message("tool", quoted, tool_call_id=call.id))
    except LlmError as exc:
        yield run.finish("failed", exc.code, exc.message)
    except ApiError as exc:
        yield run.finish("failed", exc.code, exc.message)
