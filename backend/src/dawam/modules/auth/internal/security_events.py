"""The security-event recorder: the one way any module writes a ``SecurityEvent``."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock

from ..tables import (
    EVENT_TYPE_MAX_LENGTH,
    IP_MAX_LENGTH,
    TARGET_TYPE_MAX_LENGTH,
    SecurityEventRecord,
)

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Metadata keys naming any of these are refused: events must never hold a secret.
_SECRET_WORDS = ("password", "secret", "token")


@dataclass(frozen=True)
class SecurityEvent:
    """One recorded security-relevant action (spec §7 ``SecurityEvent``)."""

    id: uuid.UUID
    event_type: str
    """What happened, e.g. ``login_succeeded``, ``login_failed``, ``account_locked``."""
    actor_id: uuid.UUID | None
    """The user who did it, when known (a failed sign-in has none)."""
    target_type: str | None
    """What it was done to, e.g. ``user``; None when there is no such thing."""
    target_id: uuid.UUID | None
    metadata: dict[str, Any]
    ip: str | None
    created_at: datetime


def _check_name(kind: str, value: str, max_length: int) -> None:
    if len(value) > max_length or not _NAME.match(value):
        raise ValueError(
            f"{kind} must be a snake_case name of at most {max_length} characters: {value!r}"
        )


def _check_no_secrets(metadata: Mapping[str, Any]) -> None:
    for key, value in metadata.items():
        if any(word in key.lower() for word in _SECRET_WORDS):
            raise ValueError(f"security event metadata must not hold a secret: {key!r}")
        if isinstance(value, Mapping):
            _check_no_secrets(value)


def _event(record: SecurityEventRecord) -> SecurityEvent:
    return SecurityEvent(
        id=record.id,
        event_type=record.event_type,
        actor_id=record.actor_id,
        target_type=record.target_type,
        target_id=record.target_id,
        metadata=dict(record.event_metadata),
        ip=record.ip,
        created_at=record.created_at,
    )


class SecurityEventRecorder:
    """Records security events (sign-ins, lockouts, role changes, deactivations,
    ownership reassignments, ...) for admins to review.

    Events are append-only. ``actor_id`` and ``target_id`` are plain ids, not foreign
    keys: an event outlives whatever it names.
    """

    def __init__(self, engine: sa.Engine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def record(
        self,
        event_type: str,
        *,
        actor_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
        ip: str | None = None,
        db: Session | None = None,
    ) -> SecurityEvent:
        """Record an event, now.

        Pass ``db`` to write it in that session's transaction, so it is kept only if
        the caller's change is; without it the event is committed on its own.
        ``metadata`` must be JSON-serialisable and never hold a secret: a key naming a
        password, token or secret raises ``ValueError``, as does an ``event_type`` or
        ``target_type`` that is not a short snake_case name.
        """
        _check_name("event_type", event_type, EVENT_TYPE_MAX_LENGTH)
        if target_type is not None:
            _check_name("target_type", target_type, TARGET_TYPE_MAX_LENGTH)
        metadata = dict(metadata or {})
        _check_no_secrets(metadata)
        record = SecurityEventRecord(
            id=uuid.uuid4(),
            event_type=event_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            event_metadata=metadata,
            ip=ip[:IP_MAX_LENGTH] if ip else None,
            created_at=self._clock(),
        )
        if db is not None:
            db.add(record)
            db.flush()
            return _event(record)
        with Session(self._engine) as own, own.begin():
            own.add(record)
            own.flush()
            return _event(record)

    def recent(self, limit: int = 100) -> list[SecurityEvent]:
        """The latest ``limit`` events, newest first."""
        with Session(self._engine) as db:
            records = db.scalars(
                sa.select(SecurityEventRecord)
                .order_by(SecurityEventRecord.created_at.desc(), SecurityEventRecord.seq.desc())
                .limit(limit)
            )
            return [_event(record) for record in records]
