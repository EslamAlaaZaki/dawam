"""Object handlers: how the engine reads and changes each type of object.

The engine knows nothing about Source Schema tables, KPIs or DW tables. A module that
owns objects Change Sets may change registers one ``ObjectHandler`` per object type;
the engine asks it for the policy action an operation needs (so the item's required role
comes from the permission matrix), for the object's current values (staleness), and to
apply an accepted item. Every method runs in the session the engine passes, so an
accept is one transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError


@dataclass(frozen=True)
class AppliedChange:
    """What applying an item did, for the audit trail (only the fields that changed;
    ``old`` is ``None`` for a create and ``new`` for a delete; never secrets)."""

    entity_type: str
    entity_id: str | uuid.UUID
    old: Mapping[str, Any] | None
    new: Mapping[str, Any] | None
    label: str = ""


class ObjectHandler(Protocol):
    object_type: str
    """The type this handler serves, e.g. ``source_table``."""

    def required_action(self, operation: str) -> Action:
        """The policy action ``operation`` on this type needs; the item's required role is
        the lowest role that may perform it. ``ValueError`` for an operation the type does
        not support."""
        ...

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        """Refuse a proposed item that could never apply (``ApiError`` 422), so the
        proposer hears about it immediately."""
        ...

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        """The object's values of ``fields`` now, as JSON, locking its row for the
        transaction; ``None`` if it is gone (the item is then stale)."""
        ...

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        """Make the change in ``db``'s transaction. An ``ApiError`` aborts the whole
        accept."""
        ...


class ObjectHandlers:
    """The handlers of an installation, by object type."""

    def __init__(self, *handlers: ObjectHandler) -> None:
        self._handlers: dict[str, ObjectHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: ObjectHandler) -> None:
        if handler.object_type in self._handlers:
            raise ValueError(f"a handler for {handler.object_type!r} is already registered")
        self._handlers[handler.object_type] = handler

    def get(self, object_type: str) -> ObjectHandler:
        """422 ``unknown_object_type`` for a type nobody registered."""
        handler = self._handlers.get(object_type)
        if handler is None:
            raise ApiError(
                422,
                "unknown_object_type",
                f"Change Sets cannot change objects of type `{object_type}`.",
            )
        return handler

    def object_types(self) -> list[str]:
        return sorted(self._handlers)
