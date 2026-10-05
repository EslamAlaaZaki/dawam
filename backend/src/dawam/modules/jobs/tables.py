"""The jobs module's table. Private: only this module reads or writes it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

TYPE_MAX_LENGTH = 64
TITLE_MAX_LENGTH = 200
ERROR_MAX_LENGTH = 1000
LOG_MAX_LENGTH = 200_000
"""Characters of log kept per job; the oldest lines go first."""


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="status"
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
        sa.Index("ix_jobs_status_created_at", "status", "created_at"),
        sa.Index("ix_jobs_workspace_id_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(sa.String(TYPE_MAX_LENGTH))
    """What the job does (spec §7: ``extract``, ``profile``, ``export``, ...)."""
    title: Mapped[str] = mapped_column(sa.String(TITLE_MAX_LENGTH))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """The JSON its handler gets back."""
    status: Mapped[str] = mapped_column(sa.String(16))
    progress: Mapped[int] = mapped_column()
    log: Mapped[str] = mapped_column(sa.Text)
    error: Mapped[str | None] = mapped_column(sa.String(ERROR_MAX_LENGTH))
    """Why it failed."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    """Set by the worker running the job; a running job whose heartbeat is stale lost
    its worker."""
