"""The LLM gateway: one internal format for every provider (spec §6.18).

Messages, tool calls, stream events, token usage and errors are normalised here, so no
other code ever sees a vendor's wire format or imports a vendor SDK. An ``Adapter``
speaks one provider family's protocol; the ``Gateway`` wraps an adapter for one model
and adds what every provider needs: retries with backoff for rate limits and
unavailability, and token usage (estimated when the provider reports none).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]

RATE_LIMIT = "rate_limit"
AUTH = "auth"
CONTEXT_OVERFLOW = "context_overflow"
UNAVAILABLE = "unavailable"
BAD_REQUEST = "bad_request"
INVALID_RESPONSE = "invalid_response"

RETRYABLE_CODES = frozenset({RATE_LIMIT, UNAVAILABLE})
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 30.0
CHARS_PER_TOKEN = 4


class LlmError(Exception):
    """A provider failure in the common vocabulary: ``code`` is one of ``auth``,
    ``rate_limit``, ``context_overflow``, ``unavailable``, ``bad_request`` or
    ``invalid_response``. The message never contains an API key."""

    def __init__(self, code: str, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        """Seconds the provider asked to wait (``Retry-After``), if it did."""

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call; ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    """On an ``assistant`` message: the calls the model made."""
    tool_call_id: str | None = None
    """On a ``tool`` message: the call it answers."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    """True when DAWAM counted (roughly) because the provider reported nothing."""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    """A complete tool call (arguments parsed), emitted once the model has finished it."""

    call: ToolCall


@dataclass(frozen=True)
class Done:
    usage: Usage | None
    finish_reason: str | None = None
    """``stop``, ``tool_calls``, ``length`` or ``None`` when the provider gave none."""


ChatEvent = TextDelta | ToolCallEvent | Done
"""What ``chat`` yields: any number of ``TextDelta`` and ``ToolCallEvent``, then one
``Done``. Without streaming, the text arrives as a single ``TextDelta``."""


@dataclass(frozen=True)
class Capabilities:
    """What a model supports; ``None`` means not known (not tested yet)."""

    tool_calling: bool | None = None
    streaming: bool | None = None
    json_schema: bool | None = None
    context_window: int | None = None
    embedding_dimension: int | None = None


class Adapter(Protocol):
    """One provider family's protocol. Raises ``LlmError`` for every provider failure
    (including unreachable servers) and never retries: the ``Gateway`` does."""

    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]: ...

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]: ...

    def context_window(self, model: str) -> int | None:
        """The model's context window from the server's metadata, if it reports one."""
        ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def _message_text(message: Message) -> str:
    calls = "".join(f"{c.name}{json.dumps(c.arguments)}" for c in message.tool_calls)
    return (message.content or "") + calls


class Gateway:
    """The one way code talks to a model: ``chat``, ``embed`` and ``capabilities``."""

    def __init__(
        self,
        adapter: Adapter,
        *,
        model: str,
        capabilities: Capabilities | None = None,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._capabilities = capabilities or Capabilities()
        self._max_retries = max_retries
        self._sleep = sleep

    def capabilities(self) -> Capabilities:
        """What the last "Test connection" found (unknown fields are ``None``)."""
        return self._capabilities

    def server_context_window(self) -> int | None:
        """The context window the provider's own metadata reports for this model."""
        return self._adapter.context_window(self._model)

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        stream: bool = False,
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> Iterator[ChatEvent]:
        """Send the conversation and yield events. A rate limit or an unavailable provider
        is retried with backoff, but only until the first event: a stream already begun
        is never restarted. Token usage is estimated when the provider reports none."""
        events = self._retrying(
            lambda: self._adapter.chat(self._model, messages, tools, stream, json_schema)
        )
        produced: list[str] = []
        for event in events:
            if isinstance(event, TextDelta):
                produced.append(event.text)
            elif isinstance(event, ToolCallEvent):
                produced.append(f"{event.call.name}{json.dumps(event.call.arguments)}")
            elif isinstance(event, Done) and event.usage is None:
                prompt = sum(estimate_tokens(_message_text(m)) for m in messages)
                completion = estimate_tokens("".join(produced))
                event = Done(Usage(prompt, completion, estimated=True), event.finish_reason)
            yield event

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Retried like ``chat``."""
        [vectors] = self._retrying(lambda: iter([self._adapter.embed(self._model, texts)]))
        return vectors

    def _retrying[T](self, start: Callable[[], Iterator[T]]) -> Iterator[T]:
        attempt = 0
        while True:
            try:
                events = start()
                first = next(events)
            except StopIteration:
                return iter(())
            except LlmError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                delay = exc.retry_after
                if delay is None:
                    delay = BACKOFF_BASE_SECONDS * 2**attempt
                self._sleep(min(delay, BACKOFF_MAX_SECONDS))
                attempt += 1
                continue
            return _prepend(first, events)


def _prepend[T](first: T, rest: Iterator[T]) -> Iterator[T]:
    yield first
    yield from rest


@dataclass(frozen=True)
class ProbeResult:
    """What "Test connection" learned about a model."""

    ok: bool
    capabilities: Capabilities
    error_code: str | None = None
    error: str | None = None


_PROBE_TOOL = ToolSpec(
    name="echo",
    description="Repeat the given text back.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)
_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _collect(events: Iterator[ChatEvent]) -> tuple[str, list[ToolCall]]:
    text: list[str] = []
    calls: list[ToolCall] = []
    for event in events:
        if isinstance(event, TextDelta):
            text.append(event.text)
        elif isinstance(event, ToolCallEvent):
            calls.append(event.call)
    return "".join(text), calls


def probe(
    gateway: Gateway,
    *,
    chat: bool,
    embedding: bool,
    context_window: int | None = None,
) -> ProbeResult:
    """Run "Test connection": a short prompt, a streamed one, a dummy tool call, a
    JSON-schema request and, for embedding models, one embedding. A failure of the
    short prompt (or of the embedding) fails the test with its error code; failures of
    the optional features just mean the model does not support them.
    ``context_window`` (the admin's input) wins over the server's metadata."""
    tool_calling = streaming = json_schema = dimension = None
    try:
        if chat:
            reply, _ = _collect(
                gateway.chat([Message("user", "Reply with the single word OK.")], stream=False)
            )
            if not reply.strip():
                raise LlmError(INVALID_RESPONSE, "The model answered with an empty reply.")
            streaming = _supported(
                lambda: bool(
                    _collect(gateway.chat([Message("user", "Say OK.")], stream=True))[0].strip()
                )
            )
            tool_calling = _supported(
                lambda: any(
                    call.name == _PROBE_TOOL.name
                    for call in _collect(
                        gateway.chat(
                            [Message("user", "Call the echo tool with the text 'ping'.")],
                            [_PROBE_TOOL],
                        )
                    )[1]
                )
            )
            json_schema = _supported(
                lambda: _is_probe_json(
                    _collect(
                        gateway.chat(
                            [Message("user", 'Answer with JSON: {"ok": true}.')],
                            json_schema=_PROBE_SCHEMA,
                        )
                    )[0]
                )
            )
        if embedding:
            [vector] = gateway.embed(["ping"])
            if not vector:
                raise LlmError(INVALID_RESPONSE, "The model returned an empty embedding.")
            dimension = len(vector)
        window = context_window
        if window is None and chat:
            try:
                window = gateway.server_context_window()
            except LlmError:
                window = None
    except LlmError as exc:
        return ProbeResult(
            ok=False,
            capabilities=Capabilities(context_window=context_window),
            error_code=exc.code,
            error=exc.message,
        )
    return ProbeResult(
        ok=True,
        capabilities=Capabilities(
            tool_calling=tool_calling,
            streaming=streaming,
            json_schema=json_schema,
            context_window=window,
            embedding_dimension=dimension,
        ),
    )


def _supported(attempt: Callable[[], bool]) -> bool:
    """Whether an optional feature works: it must succeed without any provider error."""
    try:
        return attempt()
    except LlmError:
        return False


def _is_probe_json(text: str) -> bool:
    try:
        value = json.loads(text)
    except ValueError:
        return False
    return isinstance(value, dict) and isinstance(value.get("ok"), bool)
