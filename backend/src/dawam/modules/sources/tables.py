"""The sources module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

NAME_MAX_LENGTH = 200
CODE_MAX_LENGTH = 24
DESCRIPTION_MAX_LENGTH = 4000
OWNER_MAX_LENGTH = 200

CODE_CONSTRAINT = "uq_source_systems_workspace_id"
"""Violated by a System Code another Source System of the Workspace already has."""


class SourceSystemRecord(Base):
    __tablename__ = "source_systems"
    __table_args__ = (
        sa.UniqueConstraint("workspace_id", "code"),
        sa.CheckConstraint("status IN ('present', 'deleted')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    code: Mapped[str] = mapped_column(sa.String(CODE_MAX_LENGTH))
    """The System Code: unique per Workspace, identifier-safe."""
    description: Mapped[str] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH))
    business_owner: Mapped[str] = mapped_column(sa.String(OWNER_MAX_LENGTH))
    technical_owner: Mapped[str] = mapped_column(sa.String(OWNER_MAX_LENGTH))
    status: Mapped[str] = mapped_column(sa.String(16))
    """``present`` or ``deleted`` (a soft delete; spec §7)."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""


HOST_MAX_LENGTH = 253
IDENTIFIER_MAX_LENGTH = 128
SYSTEM_CONNECTION_CONSTRAINT = "uq_connections_source_system_id"


class ConnectionRecord(Base):
    """A Source System's live database Connection (one per system, spec story 40)."""

    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_systems.id", ondelete="CASCADE"), unique=True
    )
    engine: Mapped[str] = mapped_column(sa.String(32))
    host: Mapped[str] = mapped_column(sa.String(HOST_MAX_LENGTH))
    port: Mapped[int] = mapped_column()
    database: Mapped[str] = mapped_column(sa.String(IDENTIFIER_MAX_LENGTH))
    username: Mapped[str] = mapped_column(sa.String(IDENTIFIER_MAX_LENGTH))
    secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    """The password sealed with ``dawam.platform.crypto.SecretBox`` (AES-256-GCM, context
    ``connection.password``); ``None`` when the database needs none. Never returned."""
    options: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    allowed_schemas: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.String(IDENTIFIER_MAX_LENGTH)))
    can_write: Mapped[bool | None] = mapped_column()
    """From the last successful test: could the user change data? ``None``: never tested."""
    last_tested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
