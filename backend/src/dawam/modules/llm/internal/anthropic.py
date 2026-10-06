"""The ``anthropic`` adapter: Claude through the Anthropic Messages API (``/messages``).

Chat with tools, streaming and structured output; Anthropic has no embeddings endpoint,
so ``embed`` refuses. The base URL includes the version prefix
(``https://api.anthropic.com/v1``). The key goes in ``x-api-key``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from ..gateway import (
    BAD_REQUEST,
    INVALID_RESPONSE,
    UNAVAILABLE,
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
from .transport import HttpResponse, Transport, TransportError
from .wire import detail, error_for, json_body

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
"""The API requires a cap on the reply; this one fits every Claude model."""

_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


class AnthropicAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        transport: Transport,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    # -- Adapter ---------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]:
        system, wire_messages = _wire_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": wire_messages,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        if json_schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
        response = self._send("POST", "/messages", json.dumps(body).encode("utf-8"))
        if response.status >= 400:
            try:
                raise error_for(response)
            finally:
                response.close()
        try:
            if stream:
                yield from _stream_events(response)
            else:
                yield from _reply_events(json_body(response))
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        raise LlmError(BAD_REQUEST, "Anthropic has no embeddings: register an embedding model.")

    def context_window(self, model: str) -> int | None:
        try:
            response = self._send("GET", f"/models/{model}", None)
        except LlmError:
            return None
        try:
            if response.status != 200:
                return None
            data = json_body(response)
        except (LlmError, OSError):
            return None
        finally:
            response.close()
        value = data.get("max_input_tokens") if isinstance(data, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    # -- HTTP ------------------------------------------------------------------------

    def _send(self, method: str, path: str, body: bytes | None) -> HttpResponse:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-version": API_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            return self._transport.request(
                method, self._base_url + path, headers=headers, body=body, timeout=self._timeout
            )
        except TransportError as exc:
            raise LlmError(UNAVAILABLE, str(exc)) from None


def _wire_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """System messages become the top-level ``system``; a tool result is a ``user`` block,
    and consecutive ``user`` turns merge (Anthropic wants the results in one message)."""
    system: list[str] = []
    wire: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if wire and wire[-1]["role"] == role == "user":
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": blocks})

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "user":
            add("user", [{"type": "text", "text": message.content or ""}])
        elif message.role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content or "",
                    }
                ],
            )
        else:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in message.tool_calls
            )
            add("assistant", blocks or [{"type": "text", "text": ""}])
    return "\n\n".join(system), wire


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage(raw: Any, previous: Usage | None = None) -> Usage | None:
    """Prompt tokens include cached input; ``previous`` fills in what ``raw`` omits (a stream
    reports the input in ``message_start`` and the output in ``message_delta``)."""
    if not isinstance(raw, dict):
        return previous
    has_input = "input_tokens" in raw
    prompt = (
        _count(raw.get("input_tokens"))
        + _count(raw.get("cache_creation_input_tokens"))
        + _count(raw.get("cache_read_input_tokens"))
        if has_input
        else (previous.prompt_tokens if previous else None)
    )
    completion = raw.get("output_tokens")
    if not isinstance(completion, int) or isinstance(completion, bool):
        completion = previous.completion_tokens if previous else None
    if prompt is None or completion is None:
        return previous
    return Usage(prompt, completion)


def _tool_call(block: Any, arguments: Any) -> ToolCall:
    try:
        if not isinstance(arguments, dict):
            raise ValueError("arguments are not an object")
        return ToolCall(id=str(block.get("id") or ""), name=str(block["name"]), arguments=arguments)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LlmError(INVALID_RESPONSE, "The model made a tool call that is not valid.") from None


def _finish(stop_reason: Any) -> str | None:
    return _FINISH_REASONS.get(stop_reason, stop_reason) if isinstance(stop_reason, str) else None


def _reply_events(data: Any) -> Iterator[ChatEvent]:
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
    for block in content:
        if not isinstance(block, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
        if block.get("type") == "text" and block.get("text"):
            yield TextDelta(str(block["text"]))
        elif block.get("type") == "tool_use":
            yield ToolCallEvent(_tool_call(block, block.get("input")))
    yield Done(_usage(data.get("usage")), _finish(data.get("stop_reason")))


def _stream_events(response: HttpResponse) -> Iterator[ChatEvent]:
    blocks: dict[int, dict[str, Any]] = {}
    usage: Usage | None = None
    finish: str | None = None
    finished = False
    for line in response.iter_lines():
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            continue
        try:
            event = json.loads(text[len("data:") :].strip())
        except ValueError:
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.") from None
        if not isinstance(event, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.")
        kind = event.get("type")
        if kind == "error":
            raise LlmError(UNAVAILABLE, "The provider failed mid-stream: " + detail(event))
        if kind == "message_start":
            message = event.get("message")
            usage = _usage(message.get("usage") if isinstance(message, dict) else None, usage)
        elif kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                blocks[_index(event)] = {"block": block, "json": ""}
        elif kind == "content_block_delta":
            delta = event.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield TextDelta(str(delta["text"]))
            elif delta.get("type") == "input_json_delta" and _index(event) in blocks:
                blocks[_index(event)]["json"] += str(delta.get("partial_json") or "")
        elif kind == "content_block_stop":
            slot = blocks.pop(_index(event), None)
            if slot is not None:
                yield ToolCallEvent(_tool_call(slot["block"], _parse_arguments(slot["json"])))
        elif kind == "message_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                finish = _finish(delta.get("stop_reason")) or finish
            usage = _usage(event.get("usage"), usage)
        elif kind == "message_stop":
            finished = True
            break
    if not finished and finish is None:
        raise LlmError(UNAVAILABLE, "The provider's stream ended unexpectedly.")
    yield Done(usage, finish)


def _index(event: dict[str, Any]) -> int:
    value = event.get("index")
    return value if isinstance(value, int) else 0


def _parse_arguments(raw: str) -> Any:
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
