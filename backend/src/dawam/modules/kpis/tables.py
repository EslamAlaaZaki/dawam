"""The kpis module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

NAME_MAX_LENGTH = 200
TEXT_MAX_LENGTH = 4000
SQL_MAX_LENGTH = 20000
UNIT_MAX_LENGTH = 50
AGGREGATION_MAX_LENGTH = 50
OWNER_MAX_LENGTH = 200
REFRESH_MAX_LENGTH = 100
TARGET_TEXT_MAX_LENGTH = 100
MAX_TARGETS = 20

STATUSES = ("draft", "in_review", "approved")
ORIGINS = ("user", "ai", "rule")


class KpiRecord(Base):
    __tablename__ = "kpis"
    __table_args__ = (
        sa.CheckConstraint("status IN ('draft', 'in_review', 'approved')", name="status"),
        sa.CheckConstraint("origin IN ('user', 'ai', 'rule')", name="origin"),
        sa.Index("ix_kpis_workspace_system", "workspace_id", "source_system_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    source_system_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("source_systems.id"))
    """The Source System the KPI is documented under; ``None``: the Data Warehouse."""
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    definition: Mapped[str] = mapped_column(sa.String(TEXT_MAX_LENGTH))
    """The business definition."""
    formula_text: Mapped[str] = mapped_column(sa.String(TEXT_MAX_LENGTH))
    """The formula in words."""
    formula_sql: Mapped[str | None] = mapped_column(sa.String(SQL_MAX_LENGTH))
    """The formula as SQL against the DW Schema; ``None`` until the DW exists."""
    unit: Mapped[str] = mapped_column(sa.String(UNIT_MAX_LENGTH))
    aggregation: Mapped[str] = mapped_column(sa.String(AGGREGATION_MAX_LENGTH))
    owner: Mapped[str] = mapped_column(sa.String(OWNER_MAX_LENGTH))
    refresh_frequency: Mapped[str] = mapped_column(sa.String(REFRESH_MAX_LENGTH))
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    """A list of ``{"label": ..., "value": ...}``."""
    origin: Mapped[str] = mapped_column(sa.String(8))
    status: Mapped[str] = mapped_column(sa.String(16))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""
