"""The memory module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

TEXT_MAX_LENGTH = 500


class MemoryItemRecord(Base):
    """One fact the team confirmed for the assistant to recall (ADR 0010). A row exists only
    once its Change Set item was accepted, so every row is approved."""

    __tablename__ = "memory_items"
    __table_args__ = (sa.Index("ix_memory_items_workspace", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(sa.String(TEXT_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    """When its Change Set item was accepted."""
