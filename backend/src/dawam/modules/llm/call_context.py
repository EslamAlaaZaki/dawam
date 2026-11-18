"""What a model call is made for: the feature and, for a background job, the job (#631).

Ambient, so no call site threads it through: the job runner sets it from the job's type and
id (``dawam.job_handlers``), the assistant around a chat answer and document search around a
search. ``MeteredGateway`` reads it when a call starts and records it on the call's
``llm_usage`` row and span. A call made with none records ``unknown``; nothing fails for it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

FEATURES = (
    "assistant",
    "catalog_suggestions",
    "pii_scan",
    "relationships",
    "kpi_suggestions",
    "file_indexing",
    "search",
)
UNKNOWN_FEATURE = "unknown"


@dataclass(frozen=True)
class CallContext:
    feature: str = UNKNOWN_FEATURE
    job_id: uuid.UUID | None = None
    """The ``jobs`` row the call is made for; ``None`` outside a background job."""


_current: ContextVar[CallContext | None] = ContextVar("dawam_ai_call_context", default=None)


def current_call_context() -> CallContext:
    return _current.get() or CallContext()


@contextmanager
def ai_call_context(feature: str, job_id: uuid.UUID | None = None) -> Iterator[None]:
    """Model calls made inside are made for ``feature`` (and ``job_id``)."""
    token = _current.set(CallContext(feature, job_id))
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def default_ai_feature(feature: str) -> Iterator[None]:
    """Like ``ai_call_context``, unless a context is already set: then that one stays. A
    search the assistant or a job makes is part of that answer or that job."""
    if _current.get() is not None:
        yield
        return
    with ai_call_context(feature):
        yield


def in_ai_call_context[T](feature: str, items: Iterator[T]) -> Iterator[T]:
    """``items`` with each step taken in ``feature``'s context: a streamed response is stepped
    from contexts of its own, where a context set across a ``yield`` would be lost."""
    context = CallContext(feature)
    try:
        while True:
            token = _current.set(context)
            try:
                item = next(items)
            except StopIteration:
                return
            finally:
                _current.reset(token)
            yield item
    finally:
        close = getattr(items, "close", None)
        if close is not None:  # an abandoned stream records its calls in this context too
            token = _current.set(context)
            try:
                close()
            finally:
                _current.reset(token)
