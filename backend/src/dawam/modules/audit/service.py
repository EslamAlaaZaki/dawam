"""The audit trail (spec §6.10): who changed what, when, how, and the old and new values.

Other modules write entries through ``record_audit``, in the transaction that makes
the change. They never touch the table.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .tables import (
    ENTITY_ID_MAX_LENGTH,
    ENTITY_TYPE_MAX_LENGTH,
    VIA_VALUES,
    AuditEntryRecord,
)

SECRET_KEYS = frozenset(
    {"password", "secret", "secret_encrypted", "ciphertext", "token", "api_key", "private_key"}
)
"""Fields no reader ever sees in a Connection entry, whatever a caller recorded."""
CONNECTION_OWNER_ONLY_KEYS = frozenset({"host", "username"})
"""Fields of a Connection entry only owners see (spec §4.3)."""
CONNECTION_ENTITY = "connection"


@dataclass(frozen=True)
class AuditEntry:
    id: uuid.UUID
    workspace_id: uuid.UUID
    actor_id: uuid.UUID | None
    via: str
    entity_type: str
    entity_id: str
    old: dict[str, Any] | None
    """The values before the change (only the fields that changed); ``None`` for a create."""
    new: dict[str, Any] | None
    """The values after the change; ``None`` for a delete."""
    created_at: datetime


@dataclass(frozen=True)
class AuditPage:
    items: list[AuditEntry]
    """Newest first."""
    next_cursor: str | None


def record_audit(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    entity_type: str,
    entity_id: str | uuid.UUID,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    at: datetime,
    via: str = "user",
    change_set_id: uuid.UUID | None = None,
) -> None:
    """Add an audit entry in ``db``'s transaction. ``old`` / ``new`` are JSON-serializable
    and never hold secrets."""
    if via not in VIA_VALUES:
        raise ValueError(f"unknown audit channel {via!r}")
    entity_id_text = str(entity_id)
    if len(entity_type) > ENTITY_TYPE_MAX_LENGTH or len(entity_id_text) > ENTITY_ID_MAX_LENGTH:
        raise ValueError("audit entity type or id is too long")
    db.add(
        AuditEntryRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            actor_id=actor_id,
            via=via,
            change_set_id=change_set_id,
            entity_type=entity_type,
            entity_id=entity_id_text,
            old=dict(old) if old is not None else None,
            new=dict(new) if new is not None else None,
            created_at=at,
        )
    )


class AuditService:
    """Reads the audit trail. Does not authorize: the caller checks the user may view
    the Workspace first."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def page(
        self,
        workspace_id: uuid.UUID,
        *,
        see_connection_details: bool,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_id: uuid.UUID | None = None,
        via: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> AuditPage:
        """The Workspace's entries, newest first, narrowed by the filters (``since`` and
        ``until`` are inclusive). Connection entries never carry secrets, and carry host
        and username only when ``see_connection_details`` (owners). Raises ``ApiError``
        422 ``invalid_cursor`` for a cursor this method did not return."""
        query = (
            sa.select(AuditEntryRecord)
            .where(AuditEntryRecord.workspace_id == workspace_id)
            .order_by(AuditEntryRecord.created_at.desc(), AuditEntryRecord.id.desc())
            .limit(limit + 1)
        )
        for column, value in (
            (AuditEntryRecord.entity_type, entity_type),
            (AuditEntryRecord.entity_id, entity_id),
            (AuditEntryRecord.actor_id, actor_id),
            (AuditEntryRecord.via, via),
        ):
            if value is not None:
                query = query.where(column == value)
        if since is not None:
            query = query.where(AuditEntryRecord.created_at >= since)
        if until is not None:
            query = query.where(AuditEntryRecord.created_at <= until)
        if cursor is not None:
            after_at, after_id = decode_cursor(cursor, 2)
            try:
                at = datetime.fromisoformat(after_at)
                after_uuid = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.") from None
            if at.tzinfo is None:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.")
            query = query.where(
                sa.tuple_(AuditEntryRecord.created_at, AuditEntryRecord.id)
                < sa.tuple_(
                    sa.literal(at, sa.DateTime(timezone=True)),
                    sa.literal(after_uuid, sa.Uuid),
                )
            )
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        page = records[:limit]
        next_cursor = None
        if len(records) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        return AuditPage(
            items=[_entry(r, see_connection_details=see_connection_details) for r in page],
            next_cursor=next_cursor,
        )

    def list(
        self, workspace_id: uuid.UUID, *, entity_type: str, entity_id: str | uuid.UUID
    ) -> list[AuditEntry]:
        """The entries of one entity, oldest first (secrets stripped, host and username
        kept: for the module that owns the entity)."""
        query = (
            sa.select(AuditEntryRecord)
            .where(
                AuditEntryRecord.workspace_id == workspace_id,
                AuditEntryRecord.entity_type == entity_type,
                AuditEntryRecord.entity_id == str(entity_id),
            )
            .order_by(AuditEntryRecord.created_at, AuditEntryRecord.id)
        )
        with Session(self._engine) as db:
            return [_entry(r, see_connection_details=True) for r in db.scalars(query)]


def _redact(
    values: dict[str, Any] | None, entity_type: str, *, see_connection_details: bool
) -> dict[str, Any] | None:
    if values is None or entity_type != CONNECTION_ENTITY:
        return values
    hidden = SECRET_KEYS if see_connection_details else SECRET_KEYS | CONNECTION_OWNER_ONLY_KEYS
    return {k: v for k, v in values.items() if k not in hidden}


def _entry(record: AuditEntryRecord, *, see_connection_details: bool) -> AuditEntry:
    return AuditEntry(
        id=record.id,
        workspace_id=record.workspace_id,
        actor_id=record.actor_id,
        via=record.via,
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        old=_redact(record.old, record.entity_type, see_connection_details=see_connection_details),
        new=_redact(record.new, record.entity_type, see_connection_details=see_connection_details),
        created_at=record.created_at,
    )
