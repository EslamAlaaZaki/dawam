"""Cursor pagination for list endpoints (spec §8.3).

A list endpoint takes ``limit`` (``PageLimit``) and ``cursor`` (``PageCursor``) query
parameters and returns its items plus ``next_cursor``, which is ``None`` on the last
page. A cursor is opaque to clients: it encodes the sort key of the last item
returned (``encode_cursor``), and the next page starts after it (keyset pagination,
so concurrent inserts never shift a page).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated

from fastapi import Query

from dawam.platform.errors import ApiError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

PageLimit = Annotated[
    int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many items to return at most.")
]
PageCursor = Annotated[
    str | None,
    Query(max_length=1024, description="`next_cursor` of the previous page; omit for the first."),
]


def encode_cursor(*values: str) -> str:
    """An opaque cursor holding ``values`` (the sort key of the last item returned)."""
    raw = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, length: int) -> tuple[str, ...]:
    """The ``length`` values ``encode_cursor`` put in ``cursor``.

    Raises ``ApiError`` 422 ``invalid_cursor`` for anything else.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
    except (binascii.Error, ValueError):
        values = None
    if (
        not isinstance(values, list)
        or len(values) != length
        or not all(isinstance(value, str) for value in values)
    ):
        raise ApiError(422, "invalid_cursor", "The cursor is not valid; start from the first page.")
    return tuple(values)
