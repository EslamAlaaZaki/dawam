"""The ``openai_compatible`` adapter: ``/chat/completions`` and ``/embeddings``.

Covers vLLM, SGLang, Ollama (its ``/v1`` endpoint), LM Studio, llama.cpp, OpenAI,
OpenRouter, Groq, Together, Mistral, DeepSeek and anything else that speaks that
protocol. The base URL includes the version prefix (``https://api.openai.com/v1``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from ..gateway import (
    AUTH,
    BAD_REQUEST,
    CONTEXT_OVERFLOW,
    INVALID_RESPONSE,
    RATE_LIMIT,
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

MAX_DETAIL_CHARS = 300
_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "max_model_len",
    "too many tokens",
    "prompt is too long",
    "reduce the length",
)
_CONTEXT_WINDOW_KEYS = ("max_model_len", "context_length", "context_window", "max_context_length")


class OpenAICompatibleAdapter:
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
        body: dict[str, Any] = {
            "model": model,
            "messages": [_wire_message(m) for m in messages],
            "stream": stream,
        }
        if tools:
            body["tools"] = [_wire_tool(t) for t in tools]
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        if stream:
            body["stream_options"] = {"include_usage": True}
        response = self._post("/chat/completions", body)
        try:
            if stream:
                yield from _stream_events(response)
            else:
                yield from _reply_events(_json(response))
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        response = self._post("/embeddings", {"model": model, "input": list(texts)})
        try:
            data = _json(response)
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()
        try:
            rows = sorted(data["data"], key=lambda row: row.get("index", 0))
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise LlmError(
                INVALID_RESPONSE, "The provider's embeddings reply was malformed."
            ) from None
        if len(vectors) != len(texts):
            raise LlmError(INVALID_RESPONSE, "The provider returned a different number of vectors.")
        return vectors

    def context_window(self, model: str) -> int | None:
        try:
            response = self._send("GET", "/models", None)
        except LlmError:
            return None
        try:
            if response.status != 200:
                return None
            data = _json(response)
        except (LlmError, OSError):
            return None
        finally:
            response.close()
        entries = data.get("data") if isinstance(data, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("id") == model:
                for key in _CONTEXT_WINDOW_KEYS:
                    value = entry.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        return value
        return None

    # -- HTTP ------------------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any]) -> HttpResponse:
        response = self._send("POST", path, json.dumps(body).encode("utf-8"))
        if response.status >= 400:
            try:
                raise _error_for(response)
            finally:
                response.close()
        return response

    def _send(self, method: str, path: str, body: bytes | None) -> HttpResponse:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            return self._transport.request(
                method, self._base_url + path, headers=headers, body=body, timeout=self._timeout
            )
        except TransportError as exc:
            raise LlmError(UNAVAILABLE, str(exc)) from None


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _wire_message(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
        if not message.content:
            wire["content"] = None
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _json(response: HttpResponse) -> Any:
    try:
        return json.loads(response.read())
    except ValueError:
        raise LlmError(INVALID_RESPONSE, "The provider's reply was not valid JSON.") from None


def _detail(payload: Any) -> str:
    """The provider's own explanation, trimmed (it never holds our API key)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if not isinstance(message, str) and isinstance(payload, dict):
        message = payload.get("message") or payload.get("detail")
    return message[:MAX_DETAIL_CHARS] if isinstance(message, str) else ""


def _error_for(response: HttpResponse) -> LlmError:
    try:
        raw = response.read().decode("utf-8", "replace")
    except OSError:
        raw = ""
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        payload = None
    detail = _detail(payload)
    suffix = f" ({detail})" if detail else ""
    status = response.status
    if status in (401, 403):
        return LlmError(AUTH, "The provider rejected the API key." + suffix)
    if status == 429:
        return LlmError(
            RATE_LIMIT,
            "The provider is rate limiting requests." + suffix,
            retry_after=_retry_after(response.headers.get("retry-after")),
        )
    lowered = (raw or "").lower()
    if status in (400, 413, 422) and any(marker in lowered for marker in _CONTEXT_MARKERS):
        return LlmError(CONTEXT_OVERFLOW, "The conversation is too long for the model." + suffix)
    if status >= 500:
        return LlmError(UNAVAILABLE, f"The provider failed (HTTP {status})." + suffix)
    return LlmError(BAD_REQUEST, f"The provider refused the request (HTTP {status})." + suffix)


def _retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int):
        return Usage(prompt, completion)
    return None


def _tool_call(raw: Any) -> ToolCall:
    try:
        function = raw["function"]
        arguments = function.get("arguments") or "{}"
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(parsed, dict):
            raise ValueError("arguments are not an object")
        return ToolCall(id=str(raw.get("id") or ""), name=str(function["name"]), arguments=parsed)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LlmError(INVALID_RESPONSE, "The model made a tool call that is not valid.") from None


def _reply_events(data: Any) -> Iterator[ChatEvent]:
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.") from None
    content = message.get("content")
    if isinstance(content, str) and content:
        yield TextDelta(content)
    for raw in message.get("tool_calls") or []:
        yield ToolCallEvent(_tool_call(raw))
    yield Done(_usage(data.get("usage")), choice.get("finish_reason"))


def _stream_events(response: HttpResponse) -> Iterator[ChatEvent]:
    pending: dict[int, dict[str, Any]] = {}
    usage: Usage | None = None
    finish: str | None = None
    finished = False
    for line in response.iter_lines():
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            continue
        payload = text[len("data:") :].strip()
        if payload == "[DONE]":
            finished = True
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.") from None
        if not isinstance(chunk, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.")
        if "error" in chunk:
            raise LlmError(UNAVAILABLE, "The provider failed mid-stream: " + _detail(chunk))
        usage = _usage(chunk.get("usage")) or usage
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield TextDelta(content)
            for part in delta.get("tool_calls") or []:
                slot = pending.setdefault(
                    int(part.get("index", 0)), {"id": "", "name": "", "arguments": ""}
                )
                function = part.get("function") or {}
                slot["id"] = part.get("id") or slot["id"]
                slot["name"] += function.get("name") or ""
                slot["arguments"] += function.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if not finished and finish is None:
        raise LlmError(UNAVAILABLE, "The provider's stream ended unexpectedly.")
    for _, slot in sorted(pending.items()):
        yield ToolCallEvent(
            _tool_call(
                {
                    "id": slot["id"],
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
            )
        )
    yield Done(usage, finish)
