"""The LLM gateway: one internal format for every provider (spec §6.18).

Messages, tool calls, stream events, token usage and errors are normalised here, so no
other code ever sees a vendor's wire format or imports a vendor SDK. An ``Adapter``
speaks one provider family's protocol; the ``Gateway`` wraps an adapter for one model
and adds what every provider needs: retries with backoff for rate limits and
unavailability, and token usage (estimated when the provider reports none).
"""

from __future__ import annotations

import inspect
import json
import struct
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
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
IMAGE_TOKENS = 1000
"""A rough count for one page image, for when the provider reports no usage."""


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


class ListingUnsupported(Exception):
    """The adapter cannot list the provider's models (Azure OpenAI deployments, Vertex AI,
    a plugin without listing): the admin types the model name instead."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    """Non-empty, of ``[A-Za-z0-9_-]`` only (every adapter makes it so), so a history one
    provider wrote is accepted by every other."""
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None
    """Gemini's ``thoughtSignature`` for this call, sent back only to Gemini. ``None``: a call
    Gemini did not make; ``""``: one it made without a signature."""


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call; ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Image:
    """An image sent to a vision model: ``media_type`` is ``image/png``, ``image/jpeg``,
    ``image/gif`` or ``image/webp``."""

    media_type: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    """On an ``assistant`` message: the calls the model made."""
    tool_call_id: str | None = None
    """On a ``tool`` message: the call it answers."""
    images: tuple[Image, ...] = ()
    """On a ``user`` message: images for a vision model, sent before the text."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    """True when DAWAM counted (roughly) because the provider reported nothing."""
    cache_read_tokens: int = 0
    """Of ``prompt_tokens``, the part served from the provider's prompt cache."""
    cache_write_tokens: int = 0
    """Of ``prompt_tokens``, the part written to the provider's prompt cache."""
    reasoning_tokens: int = 0
    """Of ``completion_tokens``, the part spent on reasoning (reported, or estimated from the
    reasoning text). Already inside ``completion_tokens``: never add it to the total."""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """A piece of the model's reasoning ("thinking"). The ``Gateway`` passes it on only when
    the model is set to show its reasoning; otherwise it is counted and dropped."""

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


ChatEvent = TextDelta | ReasoningDelta | ToolCallEvent | Done
"""What ``chat`` yields: any number of ``TextDelta``, ``ReasoningDelta`` (only for a model set
to show its reasoning) and ``ToolCallEvent``, then one ``Done``. Without streaming, the text
arrives as a single ``TextDelta``."""

REASONING_EFFORTS = ("low", "medium", "high")


@dataclass(frozen=True)
class ReasoningSettings:
    """How a reasoning model is asked to think. An adapter turns it into its provider's
    request: Anthropic adaptive thinking with ``output_config.effort`` (Claude 4.6 and newer)
    or a ``budget_tokens`` (older Claude), the same through Bedrock, Gemini's ``thinkingLevel``
    (Gemini 3) or ``thinkingBudget`` (Gemini 2.5), or OpenAI-style ``reasoning_effort``.
    ``<think>`` tags (Qwen3, DeepSeek-R1 style servers) are split out of every reply."""

    effort: str | None = None
    """``low``, ``medium`` or ``high``; ``None``: the provider's default."""
    budget_tokens: int | None = None
    """A cap on thinking tokens, for providers that take one (older Claude, Gemini 2.5);
    ``None``: derived from the effort."""
    show: bool = False
    """Stream the reasoning as ``ReasoningDelta`` events; off: it is counted, then dropped."""


@dataclass(frozen=True)
class Capabilities:
    """What a model supports; ``None`` means not known (not tested yet)."""

    tool_calling: bool | None = None
    streaming: bool | None = None
    json_schema: bool | None = None
    context_window: int | None = None
    embedding_dimension: int | None = None
    reasoning: bool | None = None
    """``True``: "Test connection" found the model reasons; never ``False`` from a test (a
    missed detection must not untick the admin's choice)."""


@dataclass(frozen=True)
class OllamaContext:
    """What an Ollama server says about a model: ``num_ctx`` is the context it is
    configured to run with (``None``: not set, so Ollama's small default applies)."""

    num_ctx: int | None


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
        reasoning: ReasoningSettings | None = None,
    ) -> Iterator[ChatEvent]: ...

    # An adapter that supports reasoning models also takes ``reasoning: ReasoningSettings | None
    # = None`` on ``chat``; the ``Gateway`` passes it only when the model has settings.

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]: ...

    def context_window(self, model: str) -> int | None:
        """The model's context window from the server's metadata, if it reports one."""
        ...

    # Optional: ``vision(self, model, prompt, images) -> Iterator[ChatEvent]`` (one user turn
    # with ``images`` and the ``prompt``; absent: the provider cannot read images).
    # Optional: ``rerank(self, model, query, documents) -> list[float]`` (one relevance score per
    # document, in order, higher is better; absent: the provider has no rerank endpoint).
    # Optional: ``count_tokens(self, model, text) -> int | None`` (the provider's own count of
    # ``text``; ``None``/absent: not available, the gateway estimates).
    # Optional: ``list_models(self) -> list[str]`` (the names the provider offers); an
    # adapter without it, or raising ``ListingUnsupported``, is typed by hand.
    # Optional: ``ollama_context(self, model) -> OllamaContext | None`` (``None``: the
    # server is not Ollama).
    # Optional: ``reasoning_support(self, model) -> bool | None`` (whether the provider's
    # model metadata says the model reasons; ``None``: it does not say; raises ``LlmError``
    # when the provider cannot be asked).


def _takes_reasoning(adapter: Adapter) -> bool:
    try:
        return "reasoning" in inspect.signature(adapter.chat).parameters
    except (AttributeError, TypeError, ValueError):
        return False


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def _message_text(message: Message) -> str:
    calls = "".join(f"{c.name}{json.dumps(c.arguments)}" for c in message.tool_calls)
    return (message.content or "") + calls


def estimate_prompt_tokens(messages: Sequence[Message]) -> int:
    """A rough count of what ``messages`` cost as a prompt, images included."""
    return sum(estimate_tokens(_message_text(m)) + IMAGE_TOKENS * len(m.images) for m in messages)


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
        reasoning: ReasoningSettings | None = None,
    ) -> None:
        self._adapter = adapter
        self._reasoning = reasoning
        # A plugin adapter written before reasoning models does not take the argument.
        self._takes_reasoning = _takes_reasoning(adapter)
        self._model = model
        self._capabilities = capabilities or Capabilities()
        self._max_retries = max_retries
        self._sleep = sleep

    def capabilities(self) -> Capabilities:
        """What the last "Test connection" found (unknown fields are ``None``)."""
        return self._capabilities

    def list_models(self) -> list[str]:
        """The model names the provider lists (optional adapter capability).
        Raises ``ListingUnsupported`` when the adapter cannot list, ``LlmError`` on failure."""
        lister = getattr(self._adapter, "list_models", None)
        if lister is None:
            raise ListingUnsupported("This provider cannot list its models.")
        return list(lister())

    def server_context_window(self) -> int | None:
        """The context window the provider's own metadata reports for this model."""
        return self._adapter.context_window(self._model)

    def ollama_context(self) -> OllamaContext | None:
        """The configured context when the server is Ollama, else ``None`` (also when it
        cannot be told). Never raises."""
        reader = getattr(self._adapter, "ollama_context", None)
        if reader is None:
            return None
        try:
            return reader(self._model)  # type: ignore[no-any-return]
        except LlmError:
            return None

    def server_reasoning(self) -> bool | None:
        """Whether the provider's model metadata says the model reasons (``None``: it does
        not say, or the adapter cannot ask). Raises ``LlmError`` when the provider fails."""
        reader = getattr(self._adapter, "reasoning_support", None)
        if reader is None:
            return None
        value = reader(self._model)
        return value if isinstance(value, bool) else None

    def count_tokens(self, text: str) -> int:
        """Tokens in ``text``: the provider's own count when its adapter has one, else the
        chars/4 estimate. Never fails: an unknown model or a down endpoint only costs accuracy."""
        counter = getattr(self._adapter, "count_tokens", None)
        if counter is not None and text:
            try:
                counted = counter(self._model, text)
            except Exception:  # a broken tokenizer or endpoint only costs accuracy
                counted = None
            if isinstance(counted, int) and not isinstance(counted, bool) and counted >= 0:
                return counted
        return estimate_tokens(text)

    def _with_effort(self, effort: str | None) -> ReasoningSettings | None:
        """The model's reasoning settings with this call's effort in place of the stored one
        (``None`` when it is not a reasoning model). No effort: the settings as they are."""
        if self._reasoning is None:
            return None
        return (
            replace(self._reasoning, effort=effort)
            if effort in REASONING_EFFORTS
            else self._reasoning
        )

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        stream: bool = False,
        *,
        json_schema: dict[str, Any] | None = None,
        max_retries: int | None = None,
        keep_reasoning: bool = False,
        effort: str | None = None,
    ) -> Iterator[ChatEvent]:
        """Send the conversation and yield events. A rate limit or an unavailable provider
        is retried with backoff, but only until the first event: a stream already begun
        is never restarted. ``max_retries`` overrides the gateway's own for this call (``0``:
        no retry and no wait, when another model can take over). Token usage is estimated
        when the provider reports none. ``keep_reasoning``: yield ``ReasoningDelta`` even when
        the model is not set to show it (the probe looks for it). ``effort`` (``low``,
        ``medium`` or ``high``) is this call's reasoning effort; it replaces the model's own
        and is ignored when the model is not a reasoning model."""
        settings = self._with_effort(effort)
        extra: dict[str, Any] = (
            {"reasoning": settings} if settings and self._takes_reasoning else {}
        )
        events = self._estimated(
            self._retrying(
                lambda: self._adapter.chat(
                    self._model, messages, tools, stream, json_schema, **extra
                ),
                max_retries,
            ),
            messages,
        )
        show = keep_reasoning or (self._reasoning is not None and self._reasoning.show)
        for event in events:
            if show or not isinstance(event, ReasoningDelta):
                yield event

    def vision(
        self, prompt: str, images: Sequence[Image], *, effort: str | None = None
    ) -> Iterator[ChatEvent]:
        """Send ``images`` (page images, say) with ``prompt`` in one user turn and yield the
        reply's events, without streaming. Retried and estimated like ``chat``; ``effort`` is
        this call's reasoning effort, as for ``chat``. Raises
        ``LlmError`` ``bad_request`` when the provider's adapter cannot read images."""
        reader = getattr(self._adapter, "vision", None)
        if reader is None:
            raise LlmError(BAD_REQUEST, "This provider cannot read images.")
        extra: dict[str, Any] = {}
        if self._reasoning is not None and "reasoning" in inspect.signature(reader).parameters:
            extra["reasoning"] = self._with_effort(effort)
        yield from self._estimated(
            self._retrying(lambda: reader(self._model, prompt, tuple(images), **extra)),
            [Message("user", prompt, images=tuple(images))],
        )

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """One relevance score per document, in order (higher is better), from a rerank
        model. Retried like ``chat``. Raises ``LlmError`` ``bad_request`` when the provider's
        adapter has no rerank endpoint."""
        ranker = getattr(self._adapter, "rerank", None)
        if ranker is None:
            raise LlmError(BAD_REQUEST, "This provider has no rerank endpoint.")
        [scores] = self._retrying(lambda: iter([ranker(self._model, query, tuple(documents))]))
        return list(scores)

    def _estimated(
        self, events: Iterator[ChatEvent], messages: Sequence[Message]
    ) -> Iterator[ChatEvent]:
        produced: list[str] = []
        thought: list[str] = []
        for event in events:
            if isinstance(event, TextDelta):
                produced.append(event.text)
            elif isinstance(event, ReasoningDelta):
                thought.append(event.text)
            elif isinstance(event, ToolCallEvent):
                produced.append(f"{event.call.name}{json.dumps(event.call.arguments)}")
            elif isinstance(event, Done):
                reasoning = estimate_tokens("".join(thought))
                if event.usage is None:
                    prompt = estimate_prompt_tokens(messages)
                    completion = estimate_tokens("".join(produced)) + reasoning
                    usage = Usage(prompt, completion, estimated=True, reasoning_tokens=reasoning)
                    event = Done(usage, event.finish_reason)
                elif event.usage.reasoning_tokens == 0 and reasoning:
                    # The provider counts thinking inside the output but does not split it out.
                    usage = replace(
                        event.usage,
                        reasoning_tokens=min(reasoning, event.usage.completion_tokens),
                    )
                    event = Done(usage, event.finish_reason)
            yield event

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Retried like ``chat``."""
        [vectors] = self._retrying(lambda: iter([self._adapter.embed(self._model, texts)]))
        return vectors

    def _retrying[T](
        self, start: Callable[[], Iterator[T]], max_retries: int | None = None
    ) -> Iterator[T]:
        limit = self._max_retries if max_retries is None else max_retries
        attempt = 0
        while True:
            try:
                events = start()
                first = next(events)
            except StopIteration:
                return iter(())
            except LlmError as exc:
                if not exc.retryable or attempt >= limit:
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
    warnings: tuple[str, ...] = ()
    """Features a temporary error left unconfirmed (``Capabilities`` holds ``None`` for them)."""


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


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A PNG of one colour, built by hand (no imaging library needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


_PROBE_IMAGE = Image("image/png", solid_png(64, 64, (220, 30, 30)))


def _collect(events: Iterator[ChatEvent]) -> tuple[str, list[ToolCall]]:
    text: list[str] = []
    calls: list[ToolCall] = []
    for event in events:
        if isinstance(event, TextDelta):
            text.append(event.text)
        elif isinstance(event, ToolCallEvent):
            calls.append(event.call)
    return "".join(text), calls


def _collect_reasoning(events: Iterator[ChatEvent]) -> tuple[str, bool]:
    """The reply's text, and whether it shows reasoning: reasoning text (``reasoning_content``
    / ``reasoning`` fields, ``<think>`` blocks, thinking or thought parts) or reasoning tokens."""
    text: list[str] = []
    reasoned = False
    for event in events:
        if isinstance(event, TextDelta):
            text.append(event.text)
        elif isinstance(event, ReasoningDelta):
            reasoned = reasoned or bool(event.text.strip())
        elif isinstance(event, Done) and event.usage is not None:
            reasoned = reasoned or event.usage.reasoning_tokens > 0
    return "".join(text), reasoned


def probe(
    gateway: Gateway,
    *,
    chat: bool,
    embedding: bool,
    context_window: int | None = None,
    vision: bool = False,
    rerank: bool = False,
    confirmed_only: bool = False,
) -> ProbeResult:
    """Run "Test connection": a short prompt, a streamed one, a dummy tool call, a
    JSON-schema request, for vision models a small image to read and, for embedding
    models, one embedding and for rerank models a two-document ranking. A failure of the short
    prompt (or of the image, the embedding or the ranking) fails the test with its error code;
    failures of the optional features just mean the model does not support them.
    ``context_window`` (the admin's input) wins over the server's metadata.
    ``confirmed_only`` (agent and light models): tool calling and JSON schema are ``False``
    only when confirmed (the provider rejects them, or the model fails twice); a temporary
    error leaves them ``None`` and adds a warning naming the error. Reasoning is detected for
    them too, and is only ever ``True`` or ``None`` (see ``_detect_reasoning``)."""
    tool_calling = streaming = json_schema = dimension = reasoning = None
    warnings: list[str] = []
    try:
        if chat:
            reply, reasoned = _collect_reasoning(
                gateway.chat(
                    [Message("user", "Reply with the single word OK.")],
                    stream=False,
                    keep_reasoning=True,
                )
            )
            if not reply.strip():
                raise LlmError(INVALID_RESPONSE, "The model answered with an empty reply.")
            streaming = _supported(
                lambda: bool(
                    _collect(gateway.chat([Message("user", "Say OK.")], stream=True))[0].strip()
                )
            )
            if confirmed_only:
                tool_calling = _confirmed(_try_tool_call(gateway), "tool calling", warnings)
                json_schema = _confirmed(_try_json_schema(gateway), "JSON schema", warnings)
                reasoning = _detect_reasoning(gateway, reasoned, warnings)
            else:
                tool_calling = _supported(_try_tool_call(gateway))
                json_schema = _supported(_try_json_schema(gateway))
        if vision:
            seen, _ = _collect(
                gateway.vision("What colour is this image? Answer in one word.", [_PROBE_IMAGE])
            )
            if not seen.strip():
                raise LlmError(INVALID_RESPONSE, "The model gave an empty reply to the image.")
        if rerank:
            scores = gateway.rerank("ping", ["ping", "pong"])
            if len(scores) != 2:
                raise LlmError(INVALID_RESPONSE, "The model returned the wrong number of scores.")
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
            reasoning=reasoning,
        ),
        warnings=tuple(warnings),
    )


def _detect_reasoning(gateway: Gateway, reasoned: bool, warnings: list[str]) -> bool | None:
    """``True`` when the provider's model metadata says the model reasons, or the probe's
    reply showed reasoning; otherwise ``None`` (never ``False``: a missed detection must not
    untick the admin's choice). A temporary error asking the provider adds a warning."""
    try:
        if gateway.server_reasoning():
            return True
    except LlmError as exc:
        if not reasoned and exc.code != BAD_REQUEST:
            warnings.append(
                f"Reasoning could not be checked ({exc.code}: {exc.message}). Its previous "
                "value is kept; test the connection again."
            )
    return True if reasoned else None


def _try_tool_call(gateway: Gateway) -> Callable[[], bool]:
    def attempt() -> bool:
        _, calls = _collect(
            gateway.chat(
                [Message("user", "Call the echo tool with the text 'ping'.")], [_PROBE_TOOL]
            )
        )
        return any(call.name == _PROBE_TOOL.name for call in calls)

    return attempt


def _try_json_schema(gateway: Gateway) -> Callable[[], bool]:
    def attempt() -> bool:
        text, _ = _collect(
            gateway.chat(
                [Message("user", 'Answer with JSON: {"ok": true}.')], json_schema=_PROBE_SCHEMA
            )
        )
        return _is_probe_json(text)

    return attempt


def _confirmed(attempt: Callable[[], bool], feature: str, warnings: list[str]) -> bool | None:
    """``True`` when the feature worked, ``False`` only when that is confirmed (the provider
    rejected the request as bad, or the model failed on both attempts), ``None`` after a
    temporary error (rate limit, 5xx, timeout, network, auth), with a warning."""
    for _ in range(2):
        try:
            if attempt():
                return True
        except LlmError as exc:
            if exc.code == BAD_REQUEST:
                return False
            warnings.append(
                f"{feature[0].upper()}{feature[1:]} could not be checked ({exc.code}: "
                f"{exc.message}). Its previous value is kept; test the connection again."
            )
            return None
    return False


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
