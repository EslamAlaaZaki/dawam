"""The activity module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

VERB_MAX_LENGTH = 64
OBJECT_TYPE_MAX_LENGTH = 64
OBJECT_ID_MAX_LENGTH = 64
OBJECT_LABEL_MAX_LENGTH = 300


class ActivityEventRecord(Base):
    """One thing someone did in a Workspace (spec story 38). Append-only."""

    __tablename__ = "activity_events"
    __table_args__ = (
        sa.Index("ix_activity_events_workspace_feed", "workspace_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    """Who did it; ``None`` once that user is gone."""
    verb: Mapped[str] = mapped_column(sa.String(VERB_MAX_LENGTH))
    object_type: Mapped[str] = mapped_column(sa.String(OBJECT_TYPE_MAX_LENGTH))
    object_id: Mapped[str | None] = mapped_column(sa.String(OBJECT_ID_MAX_LENGTH))
    object_label: Mapped[str] = mapped_column(sa.String(OBJECT_LABEL_MAX_LENGTH))
    """What the object was called when it happened."""
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
