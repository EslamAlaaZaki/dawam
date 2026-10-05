"""The sources module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

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
