"""Memory module: Workspace memory, the facts the team confirmed for the assistant (ADR 0010).

Public interface. Other modules import only what is re-exported here:

- ``MemoryService``: ``propose`` memory items as one pending Change Set (owners and editors;
  each proposal is its own scope, so none supersedes another), ``list`` the approved items
  and ``recall`` the ones that best match a query (any member).
- ``MemoryItemHandler``: the Change Set engine's handler for ``memory_item`` objects (create
  only); the composition root registers it.
- ``MemoryItem``, ``checked_text`` (the text rules: plain, at most ``TEXT_MAX_LENGTH``
  characters, no personal data), ``MAX_PROPOSED``, ``RECALL_LIMIT``.
- ``router``: ``GET /workspaces/{workspace_id}/memory``.

Owns the ``memory_items`` table. Imports ``auth``, ``changesets`` and ``workspaces``.
"""

from .api import router
from .service import (
    MAX_PROPOSED,
    RECALL_LIMIT,
    TEXT_MAX_LENGTH,
    MemoryItem,
    MemoryItemHandler,
    MemoryService,
    checked_text,
)

__all__ = [
    "MAX_PROPOSED",
    "RECALL_LIMIT",
    "TEXT_MAX_LENGTH",
    "MemoryItem",
    "MemoryItemHandler",
    "MemoryService",
    "checked_text",
    "router",
]
