"""The collaboration module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

BODY_MAX_LENGTH = 4000
MAX_MENTIONS = 20

OBJECT_TYPES = ("source_table", "source_column", "dw_table", "dw_column", "kpi", "mapping")
"""What a comment can be about: source tables and columns, DW tables and columns (DW
objects), KPIs and mappings (a DW column's mapping)."""


class CommentRecord(Base):
    """One comment (spec §7 ``Comment``). A thread is a root comment (``parent_id`` null)
    and its replies; only the root is resolved."""

    __tablename__ = "comments"
    __table_args__ = (
        sa.Index("ix_comments_object", "workspace_id", "object_type", "object_id", "created_at"),
        sa.Index("ix_comments_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(sa.String(32))
    object_id: Mapped[uuid.UUID]
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("comments.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    """``None`` once that user is gone."""
    body: Mapped[str] = mapped_column(sa.String(BODY_MAX_LENGTH))
    mentions: Mapped[list[str]] = mapped_column(JSONB)
    """Ids of the members the comment @mentions."""
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
