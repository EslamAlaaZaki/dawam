"""The current time, as a port so tests can move it.

Code that compares against "now" (session timeouts, token expiry, ...) takes a
``Clock`` instead of calling ``datetime.now``. The composition root passes
``system_clock``; tests pass a ``FakeClock`` and ``advance`` it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Clock = Callable[[], datetime]
"""Returns the current time as a timezone-aware ``datetime``."""


def system_clock() -> datetime:
    return datetime.now(UTC)


class FakeClock:
    """A ``Clock`` that stands still until told to move."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware datetime")
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
