"""What the adapters share: reading a JSON reply and mapping an HTTP error to ``LlmError``."""

from __future__ import annotations

import json
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
    if status in (401, 403):
        return LlmError(AUTH, "The provider rejected the API key." + suffix)
    if status == 429:
        return LlmError(
            RATE_LIMIT,
            "The provider is rate limiting requests." + suffix,
            retry_after=retry_after(response.headers.get("retry-after")),
        )
    lowered = (raw or "").lower()
    if status in (400, 413, 422) and any(marker in lowered for marker in _CONTEXT_MARKERS):
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
