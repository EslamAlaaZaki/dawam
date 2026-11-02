"""What the adapters share: reading a JSON reply, mapping an HTTP error to ``LlmError`` and
keeping tool-call ids portable between providers."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from ..gateway import (
    AUTH,
    BAD_REQUEST,
    CONTEXT_OVERFLOW,
    INVALID_RESPONSE,
    RATE_LIMIT,
    UNAVAILABLE,
    LlmError,
)
from .transport import HttpResponse

MAX_DETAIL_CHARS = 300
MAX_LIST_PAGES = 20
MAX_CALL_ID_CHARS = 64
"""Bedrock's limit on a ``toolUseId``."""
_NOT_ID = re.compile(r"[^A-Za-z0-9_-]")
"""A cap on the pages read when listing a provider's models."""
_CLOUDFLARE_BLOCK = "blocked access based on your browser's signature"
_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "max_model_len",
    "too many tokens",
    "prompt is too long",
    "reduce the length",
    "exceeds the maximum number of tokens",  # Gemini
    "too long for requested model",  # Bedrock
)


def received_call_id(raw: Any) -> str:
    """The id of a tool call a provider made, in the form every provider accepts
    (``[A-Za-z0-9_-]``, non-empty): a history one provider wrote must be accepted by the next
    model, whoever runs it. A provider that sent none (some OpenAI-compatible servers send
    ``""``) gets a fresh id."""
    cleaned = sent_call_id(str(raw)) if raw else ""
    return cleaned or f"call_{uuid.uuid4().hex[:24]}"


def sent_call_id(call_id: str | None) -> str:
    """``call_id`` as sent back to a provider that checks the form (Anthropic, Bedrock). The
    same id always maps to the same text, so a call and its result still match."""
    return _NOT_ID.sub("_", call_id or "")[:MAX_CALL_ID_CHARS] or "call"


def json_body(response: HttpResponse) -> Any:
    try:
        return json.loads(response.read())
    except ValueError:
        raise LlmError(INVALID_RESPONSE, "The provider's reply was not valid JSON.") from None


def detail(payload: Any, redact: Callable[[str], str] | None = None) -> str:
    """The provider's own explanation, trimmed. ``redact`` strips our secrets from it
    first, so a secret cut by the trimming cannot leave a fragment behind."""
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if not isinstance(message, str) and isinstance(payload, dict):
        message = payload.get("message") or payload.get("detail")
    if not isinstance(message, str):
        return ""
    return (redact(message) if redact else message)[:MAX_DETAIL_CHARS]


def error_for(response: HttpResponse, redact: Callable[[str], str] | None = None) -> LlmError:
    try:
        raw = response.read().decode("utf-8", "replace")
    except OSError:
        raw = ""
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        payload = None
    explanation = detail(payload, redact)
    suffix = f" ({explanation})" if explanation else ""
    status = response.status
    if status == 403 and _CLOUDFLARE_BLOCK in raw.lower():
        return LlmError(BAD_REQUEST, "The provider's firewall blocked the request." + suffix)
    if status in (401, 403):
        return LlmError(AUTH, "The provider rejected the API key." + suffix)
    if status == 429:
        return LlmError(
            RATE_LIMIT,
            "The provider is rate limiting requests." + suffix,
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    lowered = (raw or "").lower()
    # 413 is always "too large": Groq's per-minute token limit says so with no context
    # wording, and the agent shrinks the request and retries on this code.
    if status == 413 or (
        status in (400, 422) and any(marker in lowered for marker in _CONTEXT_MARKERS)
    ):
        return LlmError(CONTEXT_OVERFLOW, "The conversation is too long for the model." + suffix)
    if status >= 500:
        return LlmError(UNAVAILABLE, f"The provider failed (HTTP {status})." + suffix)
    return LlmError(BAD_REQUEST, f"The provider refused the request (HTTP {status})." + suffix)


def retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None
