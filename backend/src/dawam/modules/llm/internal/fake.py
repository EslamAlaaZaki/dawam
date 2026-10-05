"""A fake provider for tests: replays scripted responses and tool calls.

``FakeAdapter`` is an ``Adapter`` that never touches the network. Queue outcomes with
``script`` (a ``Reply`` or an ``LlmError`` to raise); each ``chat`` or ``embed`` call
takes the next one, and every call is recorded in ``calls``. With nothing queued it
answers like a small well-behaved model, within the capabilities it was built with:
a tool call to the first tool offered, JSON ``{"ok": true}`` for a schema request,
otherwise ``OK``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..gateway import (
    BAD_REQUEST,
    ChatEvent,
    Done,
    LlmError,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    Usage,
)


@dataclass(frozen=True)
class Reply:
    """One scripted answer. ``usage=None`` mimics a provider that reports no usage."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = field(default_factory=lambda: Usage(10, 5))
    finish_reason: str | None = None


@dataclass(frozen=True)
class FakeCall:
    kind: str
    """``chat`` or ``embed``."""
    model: str
    messages: tuple[Message, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    stream: bool = False
    json_schema: dict[str, Any] | None = None
    texts: tuple[str, ...] = ()


class FakeAdapter:
    def __init__(
        self,
        *,
        tool_calling: bool = True,
        streaming: bool = True,
        json_schema: bool = True,
        context_window: int | None = 8192,
        embedding_dimension: int = 4,
    ) -> None:
        self.supports_tools = tool_calling
        self.supports_streaming = streaming
        self.supports_json_schema = json_schema
        self.window = context_window
        self.dimension = embedding_dimension
        self._script: deque[Reply | list[float] | LlmError] = deque()
        self.calls: list[FakeCall] = []

    def script(self, *outcomes: Reply | LlmError) -> FakeAdapter:
        self._script.extend(outcomes)
        return self

    def script_embeddings(self, *vectors: list[float]) -> FakeAdapter:
        self._script.extend(vectors)
        return self

    # -- Adapter ---------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]:
        self.calls.append(
            FakeCall("chat", model, tuple(messages), tuple(tools), stream, json_schema)
        )
        outcome = (
            self._script.popleft() if self._script else self._default(tools, json_schema, stream)
        )
        if isinstance(outcome, LlmError):
            raise outcome
        if not isinstance(outcome, Reply):
            raise AssertionError("the fake provider's script holds an embedding, not a reply")
        if stream and outcome.text:
            words = outcome.text.split(" ")
            for index, word in enumerate(words):
                yield TextDelta(word if index == 0 else " " + word)
        elif outcome.text:
            yield TextDelta(outcome.text)
        for call in outcome.tool_calls:
            yield ToolCallEvent(call)
        finish = outcome.finish_reason or ("tool_calls" if outcome.tool_calls else "stop")
        yield Done(outcome.usage, finish)

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(FakeCall("embed", model, texts=tuple(texts)))
        if self._script:
            outcome = self._script.popleft()
            if isinstance(outcome, LlmError):
                raise outcome
            if isinstance(outcome, list):
                return [outcome for _ in texts]
            raise AssertionError("the fake provider's script holds a reply, not an embedding")
        return [[0.1] * self.dimension for _ in texts]

    def context_window(self, model: str) -> int | None:
        return self.window

    # -- default behaviour -------------------------------------------------------------

    def _default(
        self, tools: Sequence[ToolSpec], json_schema: dict[str, Any] | None, stream: bool
    ) -> Reply | LlmError:
        if stream and not self.supports_streaming:
            return LlmError(BAD_REQUEST, "This model does not stream.")
        if tools:
            if not self.supports_tools:
                return LlmError(BAD_REQUEST, "This model does not support tool calling.")
            arguments = {"text": "ping"} if tools[0].name == "echo" else {}
            return Reply(tool_calls=(ToolCall("call_1", tools[0].name, arguments),))
        if json_schema is not None:
            if not self.supports_json_schema:
                return LlmError(BAD_REQUEST, "This model does not support JSON schema output.")
            return Reply(text='{"ok": true}')
        return Reply(text="OK")
