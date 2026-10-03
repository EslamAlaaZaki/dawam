from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

JobHandler = Callable[[Mapping[str, Any]], None]


class UnknownJobKindError(LookupError):
    pass


class JobRunner(Protocol):
    def register(self, kind: str, handler: JobHandler) -> None:
        """Make ``handler`` run jobs of ``kind``. Called once per kind at startup."""
        ...

    def submit(self, kind: str, payload: Mapping[str, Any]) -> None:
        """Run a job of ``kind`` with ``payload`` in the background."""
        ...


class InlineJobRunner:
    """Runs each job immediately, in the caller's thread, before ``submit`` returns."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"a handler for job kind {kind!r} is already registered")
        self._handlers[kind] = handler

    def submit(self, kind: str, payload: Mapping[str, Any]) -> None:
        try:
            handler = self._handlers[kind]
        except KeyError:
            raise UnknownJobKindError(kind) from None
        handler(dict(payload))
