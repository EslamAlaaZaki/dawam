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

With ``verify`` (jobs) the loop checks its own work before it answers: after the model
wrote something with one of the ``verify.after`` tools, it runs ``run_validation`` and
``get_score`` itself and, if validation reports errors, hands them back to the model to fix,
for at most ``verify.max_rounds`` rounds. The final summary ends with the validation result.

With ``checkpoint`` the loop reports a ``Checkpoint`` after each model step and after each
tool call; given one as ``resume`` it carries on from there, so a background job whose worker
died does not repeat the tool calls it had finished.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Literal, Protocol

from dawam.modules.llm import (
    Capabilities,
    ChatEvent,
    Done,
    LlmError,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from dawam.platform.errors import ApiError
from dawam.platform.tracing import tracing

from .context import (
    cap_summary,
    estimate_tokens,
    fit_messages,
    history_drop,
    prompt_budget,
    schema_tokens,
    shorten_result,
    summary_block,
)
from .injection import Inspector, flagged_parts, rule_based_inspector, warn
from .prompt import quote_data
from .prompted import MAX_RETRIES, PromptedInvalid, correction, parse_reply, tools_prompt
from .structured import (
    KPI_CARD_TOOLS,
    KPI_CARDS_SCHEMA,
    STRUCTURE_REQUEST,
    cards_block,
    parse_cards,
)

DEFAULT_MAX_TOOL_CALLS = 25
DEFAULT_MAX_JOB_TOOL_CALLS = 150
"""The cap of a background job, which explores far more than a chat answer."""

ToolStatus = Literal["ok", "refused", "error", "skipped"]
RunStatus = Literal["completed", "tool_limit", "cancelled", "failed"]

LIMIT_MESSAGE = (
    "The tool-call limit for this request is used up. Answer with what you have already found."
)


TOOL_SEARCH_THRESHOLD = 30
"""Above this many tools, only the relevant ones are sent, plus ``find_tools``."""
FIND_TOOLS_LIMIT = 5

FIND_TOOLS = ToolSpec(
    "find_tools",
    "Search the tools you were not given up front by what you want to do (for example "
    "'validate the warehouse'). The best matches become available to call right away.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What you want to do."}},
        "required": ["query"],
    },
)


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}


def search_tools(query: str, candidates: Sequence[ToolSpec]) -> list[ToolSpec]:
    """The ``candidates`` that best match ``query`` (name hits count double), best first."""
    wanted = _words(query)
    scored: list[tuple[int, ToolSpec]] = []
    for spec in candidates:
        score = 2 * len(wanted & _words(spec.name)) + len(wanted & _words(spec.description))
        if score:
            scored.append((score, spec))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [spec for _, spec in scored[:FIND_TOOLS_LIMIT]]


Summarizer = Callable[[str | None, Sequence[Message]], str | None]
"""(earlier summary, turns to fold in) -> the new summary; ``None``: could not summarise."""

DIGEST_CHARS = 300


def _digest(outcome: ToolOutcome) -> str:
    """What a later turn may learn of a result: the part kept on the run when there is one
    (never row values), else the start of the text."""
    if outcome.saved is not None:
        text = json.dumps(outcome.saved, default=str)
    else:
        text = " ".join(outcome.content.split())
    return text[:DIGEST_CHARS]


VERIFY_TOOLS = ("run_validation", "get_score")
PROPOSALS_CHECK = "validate_proposals"
DEFAULT_VERIFY_ROUNDS = 2
VERIFY_PREFIX = "verify_"
"""The id prefix of the checks the loop runs itself: they do not count against the cap."""


@dataclass(frozen=True)
class Verify:
    """Self-verification: ``after`` are the tools whose output is checked, ``max_rounds`` the
    number of fix-and-retry rounds. ``check`` validates what the run has proposed so far as a
    whole: the error messages (none: all valid), or ``None`` when nothing was proposed."""

    after: frozenset[str]
    max_rounds: int = DEFAULT_VERIFY_ROUNDS
    check: Callable[[], list[str] | None] = lambda: None


VERIFY_REQUEST = (
    "The changes you proposed do not validate. Fix what your work caused with your "
    "tools, then give your final answer. If an error is not yours to fix, say so."
)


def _parsed(outcome: ToolOutcome | None) -> dict[str, Any] | None:
    if outcome is None or outcome.status != "ok":
        return None
    try:
        data = json.loads(outcome.content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _verification_line(
    proposed: list[str] | None, validation: dict[str, Any] | None, score: dict[str, Any] | None
) -> str:
    """The sentence the final summary ends with; what could not be checked says so."""
    parts = []
    if proposed is not None:
        parts.append(f"Proposed changes: {len(proposed)} errors.")
    if validation is None:
        warehouse = "validation could not be run"
    else:
        warehouse = (
            f"validation {validation.get('error_count', 0)} errors / "
            f"{validation.get('warning_count', 0)} warnings"
        )
    if score is not None:
        warehouse += f"; score {score.get('score')} (grade {score.get('grade')})"
    parts.append(f"Current warehouse: {warehouse}.")
    return "Verification: " + " ".join(parts)


def _counted(calls: Sequence[ToolCallRecord]) -> int:
    """The calls that count against the tool-call cap (not the loop's own checks)."""
    return sum(1 for c in calls if not c.id.startswith(VERIFY_PREFIX))


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

    # Optionally ``is_read(name, arguments) -> bool``: True for a call that saves nothing and
    # may run beside other reads of the same turn. A set without it runs every call in order.


@dataclass(frozen=True)
class ToolCallRecord:
    id: str
    name: str
    arguments: dict[str, Any]
    status: ToolStatus
    duration_ms: int
    result: dict[str, Any] | None = None
    """``ToolOutcome.saved``."""
    digest: str | None = None
    """A short digest of the result, so a later turn knows what the call found."""
    flagged: tuple[str, ...] = ()
    """What of the result looked like a prompt injection (document names or the tool)."""


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Reasoning:
    """A piece of the model's reasoning, for a model set to show it: streamed to the member,
    never saved with the answer."""

    text: str


TOO_SMALL_MESSAGE = (
    "The model's context window is too small for the assistant: its instructions and tools "
    "alone do not fit. Choose a model with a larger window."
)


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


@dataclass(frozen=True)
class Compacted:
    """The oldest ``turns`` history messages were replaced by ``summary``."""

    summary: str
    turns: int


AgentEvent = Text | Reasoning | ToolFinished | Compacted | Finished


CHECKPOINT_VERSION = 1
"""Bumped when ``Checkpoint``'s JSON changes; an older checkpoint is not resumed."""


@dataclass(frozen=True)
class Checkpoint:
    """Where a run stands after a step: the messages so far, the calls made, the calls the
    model asked for that have not run yet, and the tokens used. JSON-safe through
    ``to_json`` / ``from_json`` (jobs send no images, so none are kept)."""

    messages: tuple[Message, ...]
    calls: tuple[ToolCallRecord, ...]
    pending: tuple[ToolCall, ...]
    text: str
    prompt_tokens: int
    completion_tokens: int
    protect_from: int
    limit_hit: bool = False
    loaded: tuple[str, ...] | None = None
    """With tool search: the tools sent so far (``find_tools`` adds to them)."""
    verify_rounds: int = 0
    """Self-verification: the fix rounds used so far."""
    verified_at: int = 0
    """Self-verification: how many calls the run had when it last checked its work."""

    def to_json(self) -> dict[str, Any]:
        return {
            "version": CHECKPOINT_VERSION,
            "messages": [_message_json(m) for m in self.messages],
            "calls": [_record_json(c) for c in self.calls],
            "pending": [_call_json(c) for c in self.pending],
            "text": self.text,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "protect_from": self.protect_from,
            "limit_hit": self.limit_hit,
            "loaded": None if self.loaded is None else list(self.loaded),
            "verify_rounds": self.verify_rounds,
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Checkpoint | None:
        """``None`` for a checkpoint of another version: the run starts afresh."""
        if data.get("version") != CHECKPOINT_VERSION:
            return None
        return cls(
            messages=tuple(
                Message(
                    m["role"],
                    m["content"],
                    tuple(ToolCall(**c) for c in m["tool_calls"]),
                    m["tool_call_id"],
                )
                for m in data["messages"]
            ),
            calls=tuple(
                ToolCallRecord(**{**c, "flagged": tuple(c["flagged"])}) for c in data["calls"]
            ),
            pending=tuple(ToolCall(**c) for c in data["pending"]),
            text=data["text"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            protect_from=data["protect_from"],
            limit_hit=data["limit_hit"],
            loaded=None if data.get("loaded") is None else tuple(data["loaded"]),
            verify_rounds=data.get("verify_rounds", 0),
            verified_at=data.get("verified_at", 0),
        )


def _call_json(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _message_json(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [_call_json(c) for c in message.tool_calls],
        "tool_call_id": message.tool_call_id,
    }


def _record_json(record: ToolCallRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "arguments": record.arguments,
        "status": record.status,
        "duration_ms": record.duration_ms,
        "result": record.result,
        "digest": record.digest,
        "flagged": list(record.flagged),
    }


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


logger = logging.getLogger(__name__)

MAX_PARALLEL_READS = 8
"""Most read calls of one turn that run at the same time."""


def _find(
    arguments: dict[str, Any], all_specs: Sequence[ToolSpec], loaded: set[str]
) -> ToolOutcome:
    """Run ``find_tools``: load the best matches among the tools not yet sent."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolOutcome(
            "Invalid arguments for `find_tools`: query: a string is required", "error"
        )
    found = search_tools(query, [s for s in all_specs if s.name not in loaded])
    if not found:
        return ToolOutcome("No other tool matches. Try different words.", "ok")
    loaded.update(s.name for s in found)
    text = "\n".join(f"- {s.name}: {s.description}" for s in found)
    return ToolOutcome(f"These tools are now available to call:\n{text}", "ok")


def _run_reads(
    tools: ToolSet, calls: Sequence[ToolCall], timer: Callable[[], float]
) -> list[tuple[ToolOutcome, int]]:
    """Run ``calls`` at the same time; their outcomes and durations, in the order of ``calls``.
    The tool set opens its own database session per call (services hold the engine, never a
    session), so the calls share no session."""

    def one(call: ToolCall) -> tuple[ToolOutcome, int]:
        started = timer()
        try:
            with tracing().tool_call(call.name, call.arguments) as span:
                outcome = tools.execute(call.name, call.arguments)
                span.result(outcome.status, outcome.content)
        except Exception:  # a tool set should not raise; one that does costs only its own call
            logger.exception("assistant tool failed", extra={"tool": call.name})
            outcome = ToolOutcome(f"The `{call.name}` tool failed.", "error")
        return outcome, round((timer() - started) * 1000)

    with ThreadPoolExecutor(min(len(calls), MAX_PARALLEL_READS)) as pool:
        # Each call in a copy of the caller's context, so request-scoped log fields follow.
        futures = [pool.submit(contextvars.copy_context().run, one, call) for call in calls]
        return [future.result() for future in futures]


def _cached_counter(counter: Callable[[str], int] | None) -> Callable[[str], int]:
    """``counter`` (the gateway's real count) remembered per text, so a long conversation
    is counted once per message, not once per model call; none: the chars/4 estimate."""
    if counter is None:
        return estimate_tokens
    return lru_cache(maxsize=2048)(counter)


def _structured_answer(
    gateway: ChatGateway,
    run: _Run,
    messages: list[Message],
    answer: str,
    window: int | None,
    count: Callable[[str], int],
) -> str | None:
    """The cards block for a KPI suggestion answer, else ``None`` (also when the extra
    model call fails: the Markdown answer stands). Its usage is added to the run."""
    if not answer or not any(c.name in KPI_CARD_TOOLS and c.status == "ok" for c in run.calls):
        return None
    asked = [*messages, Message("assistant", answer), Message("user", STRUCTURE_REQUEST)]
    sent = fit_messages(asked, window, protect_from=len(asked) - 1, count=count, reserved=0)
    reply: list[str] = []
    try:
        with closing(gateway.chat(sent, (), False, json_schema=KPI_CARDS_SCHEMA)) as events:
            for event in events:
                if isinstance(event, TextDelta):
                    reply.append(event.text)
                elif isinstance(event, Done) and event.usage is not None:
                    run.prompt += event.usage.prompt_tokens
                    run.completion += event.usage.completion_tokens
    except (LlmError, ApiError):
        return None
    parsed = parse_cards("".join(reply))
    if parsed is None:
        return None
    intro, cards = parsed
    return f"{intro}\n\n{cards_block(cards)}" if intro else cards_block(cards)


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
    summary: str | None = None,
    summarize: Summarizer | None = None,
    inspect: Inspector = rule_based_inspector,
    core_tools: Collection[str] | None = None,
    checkpoint: Callable[[Checkpoint], None] | None = None,
    resume: Checkpoint | None = None,
    verify: Verify | None = None,
) -> Iterator[AgentEvent]:
    """Run the conversation ``history`` (ending with the member's message) to an answer.

    ``summary`` stands for turns left out earlier. With ``summarize`` (compaction on), turns
    that no longer fit the window are summarised into it (and reported as ``Compacted``)
    instead of being lost; if summarising fails they are dropped as without it. Every ok tool
    result goes through ``inspect`` (the light-model check, or the built-in rules): a flagged
    one still reaches the model, with a warning, and is reported on its record.

    With more than ``TOOL_SEARCH_THRESHOLD`` tools, only ``core_tools`` (none: the first
    few) are sent, plus ``find_tools``, whose matches are added to what is sent.

    ``checkpoint`` gets the run's state after each model step and each tool call; a run
    given ``resume`` starts from that state (``system`` and ``history`` are then unused):
    the calls it had not run yet run first, the finished ones are not repeated, and the
    tools ``find_tools`` loaded stay loaded."""
    run = _Run()
    capabilities = gateway.capabilities()
    window = capabilities.context_window
    stream = capabilities.streaming is not False
    count = _cached_counter(getattr(gateway, "count_tokens", None))
    prompted = capabilities.tool_calling is False  # limited mode: tools are described in text
    messages = [Message("system", system + (summary_block(summary) if summary else "")), *history]
    protect_from = len(history)  # the member's latest message
    limit_hit = False
    pending: list[ToolCall] = []
    verify_rounds = 0
    verified_at = 0
    if resume is not None:
        verify_rounds, verified_at = resume.verify_rounds, resume.verified_at
        messages = list(resume.messages)
        protect_from = resume.protect_from
        limit_hit = resume.limit_hit
        pending = list(resume.pending)
        run.text, run.calls = [resume.text], list(resume.calls)
        run.prompt, run.completion = resume.prompt_tokens, resume.completion_tokens
        summarize = None  # whatever was compacted is in the saved messages already
    all_specs = list(tools.specs())  # one snapshot: the tool list stays identical per call
    specs = {spec.name: spec for spec in all_specs}
    known_tools = set(specs)
    searching = len(all_specs) > TOOL_SEARCH_THRESHOLD
    loaded = {s.name for s in all_specs if core_tools is None or s.name in core_tools}
    if searching and core_tools is None:
        loaded = {s.name for s in all_specs[: TOOL_SEARCH_THRESHOLD // 2]}
    if searching:
        specs[FIND_TOOLS.name] = FIND_TOOLS
        known_tools.add(FIND_TOOLS.name)
    if resume is not None and resume.loaded is not None:
        loaded = set(resume.loaded) & set(known_tools)
    retries = 0
    compaction_tried = summarize is None
    last_poll = timer()

    def save() -> None:
        if checkpoint is not None:
            checkpoint(
                Checkpoint(
                    tuple(messages),
                    tuple(run.calls),
                    tuple(pending),
                    "".join(run.text),
                    run.prompt,
                    run.completion,
                    protect_from,
                    limit_hit,
                    tuple(sorted(loaded)),
                    verify_rounds,
                    verified_at,
                )
            )

    def record(call: ToolCall, outcome: ToolOutcome, duration: int) -> Iterator[AgentEvent]:
        """Report a finished call and hand its result to the model; the caller saves."""
        flagged = (
            flagged_parts(call.name, outcome.content, inspect) if outcome.status == "ok" else []
        )
        entry = ToolCallRecord(
            call.id,
            call.name,
            call.arguments,
            outcome.status,
            duration,
            outcome.saved,
            _digest(outcome),
            tuple(flagged),
        )
        run.calls.append(entry)
        yield ToolFinished(entry)
        quoted = quote_data(
            f"tool:{call.name if call.name in known_tools else 'unknown'}",
            shorten_result(outcome.content, window),
        )
        if flagged:
            quoted = warn(quoted)
        if prompted:
            messages.append(Message("user", quoted))
        else:
            messages.append(Message("tool", quoted, tool_call_id=call.id))
        pending.remove(call)

    try:
        while True:
            if pending:  # the calls of the last model step (left over when resuming)
                executed = sum(
                    1 for c in run.calls if c.status != "skipped" and c.id[:7] != VERIFY_PREFIX
                )
                is_read = getattr(tools, "is_read", None)
                while pending:
                    if is_cancelled():
                        yield run.finish("cancelled")
                        return
                    # The leading run of reads goes together; a write (or find_tools) is a
                    # barrier, so every call still sees what the calls before it did.
                    batch: list[ToolCall] = []
                    if is_read is not None:
                        for c in pending[: max(0, max_tool_calls - executed)]:
                            if c.name not in specs or not is_read(c.name, c.arguments):
                                break
                            batch.append(c)
                    if len(batch) > 1:
                        done = _run_reads(tools, batch, timer)
                        executed += len(batch)
                        for read, (outcome, duration) in zip(batch, done, strict=True):
                            yield from record(read, outcome, duration)
                        save()  # once the whole batch has finished
                        continue
                    call = pending[0]
                    if executed >= max_tool_calls:
                        limit_hit = True
                        outcome, duration = ToolOutcome(LIMIT_MESSAGE, "skipped"), 0
                    else:
                        started = timer()
                        with tracing().tool_call(call.name, call.arguments) as span:
                            if searching and call.name == FIND_TOOLS.name:
                                outcome = _find(call.arguments, all_specs, loaded)
                            else:
                                outcome = tools.execute(call.name, call.arguments)
                            span.result(outcome.status, outcome.content)
                        duration = round((timer() - started) * 1000)
                        executed += 1
                    yield from record(call, outcome, duration)
                    save()
            if is_cancelled():
                yield run.finish("cancelled")
                return
            under_cap = _counted(run.calls) < max_tool_calls
            if searching:
                visible = [s for s in all_specs if s.name in loaded] + [FIND_TOOLS]
            else:
                visible = all_specs
            offered = visible if under_cap else ()
            answer: list[str] = []
            last_poll = float("-inf")  # always look at the flag when a model call begins
            extra = tools_prompt(offered) if prompted and offered else ""
            reserved = count(extra) if extra else schema_tokens(() if prompted else offered, count)
            budget = prompt_budget(window)
            if budget is not None and count(system) + reserved > budget:
                yield run.finish("failed", "context_window_too_small", TOO_SMALL_MESSAGE)
                return
            if not compaction_tried:
                compaction_tried = True
                turns = history_drop(
                    messages, window, protect_from=protect_from, count=count, reserved=reserved
                )
                text = summarize(summary, messages[1 : 1 + turns]) if summarize and turns else None
                if text:
                    summary = cap_summary(text, window)
                    del messages[1 : 1 + turns]
                    protect_from -= turns
                    messages[0] = Message("system", system + summary_block(summary))
                    yield Compacted(summary, turns)
            sent = fit_messages(
                messages, window, protect_from=protect_from, count=count, reserved=reserved
            )
            if extra:
                head = sent[0]
                sent[0] = replace(head, content=head.content + "\n\n" + extra)
            with closing(gateway.chat(sent, () if prompted else offered, stream)) as events:
                for event in events:
                    # Reading the stop flag is a query: while text streams, not on every delta.
                    if timer() - last_poll >= delta_poll_seconds:
                        last_poll = timer()
                        if is_cancelled():
                            yield run.finish("cancelled")
                            return
                    if isinstance(event, ReasoningDelta):
                        yield Reasoning(event.text)
                    elif isinstance(event, TextDelta):
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
                if not capped and capabilities.json_schema is not False:
                    block = _structured_answer(
                        gateway, run, messages, "".join(answer), window, count
                    )
                    if block:  # the intro and cards replace the Markdown list that streamed
                        run.text[:] = [block]
                if verify is not None and not capped:
                    # Any write in the run is checked, so a retry round that wrote nothing new
                    # still ends with the latest result; only new writes show a check call.
                    wrote = any(c.name in verify.after and c.status == "ok" for c in run.calls)
                    fresh = any(
                        c.name in verify.after and c.status == "ok" for c in run.calls[verified_at:]
                    )
                    if wrote:
                        began = timer()
                        errors = verify.check()
                        if errors is not None and fresh:
                            record = ToolCallRecord(
                                f"{VERIFY_PREFIX}{len(run.calls) + 1}",
                                PROPOSALS_CHECK,
                                {},
                                "ok",
                                round((timer() - began) * 1000),
                                None,
                                f"{len(errors)} errors",
                            )
                            run.calls.append(record)
                            yield ToolFinished(record)
                        verified_at = len(run.calls)
                        if errors and verify_rounds < verify.max_rounds:
                            verify_rounds += 1
                            report = quote_data(
                                f"tool:{PROPOSALS_CHECK}",
                                shorten_result("\n".join(f"- {e}" for e in errors), window),
                            )
                            messages.append(Message("assistant", "".join(answer) or None))
                            messages.append(Message("user", f"{report}\n\n{VERIFY_REQUEST}"))
                            run.text.clear()  # the answer before the fix is not the summary
                            save()
                            continue
                        outcomes: dict[str, ToolOutcome | None] = {}
                        for name in VERIFY_TOOLS:  # the current warehouse, once, at the end
                            if name not in known_tools:
                                outcomes[name] = None
                                continue
                            began = timer()
                            with tracing().tool_call(name, {}) as span:
                                got = tools.execute(name, {})
                                span.result(got.status, got.content)
                            outcomes[name] = got
                            record = ToolCallRecord(
                                f"{VERIFY_PREFIX}{len(run.calls) + 1}",
                                name,
                                {},
                                got.status,
                                round((timer() - began) * 1000),
                                got.saved,
                                _digest(got),
                            )
                            run.calls.append(record)
                            yield ToolFinished(record)
                        verified_at = len(run.calls)
                        line = "\n\n" + _verification_line(
                            errors,
                            _parsed(outcomes["run_validation"]),
                            _parsed(outcomes["get_score"]),
                        )
                        run.text.append(line)
                        yield Text(line)
                        save()
                yield run.finish("tool_limit" if capped else "completed")
                return
            if prompted:
                messages.append(Message("assistant", "".join(answer)))
            else:
                messages.append(Message("assistant", "".join(answer) or None, tuple(pending)))
            save()  # the model is not asked for this step again
    except LlmError as exc:
        yield run.finish("failed", exc.code, exc.message)
    except ApiError as exc:
        yield run.finish("failed", exc.code, exc.message)
