"""The warehouse module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

WORKSPACE_UNIQUE = "uq_data_warehouses_workspace_id"
"""Violated by setting up a Workspace's Data Warehouse a second time."""


class DataWarehouseRecord(Base):
    """A Workspace's Data Warehouse; the row exists once "Set up Data Warehouse" is done."""

    __tablename__ = "data_warehouses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True
    )
    target_platform: Mapped[str] = mapped_column(sa.String(16))
    layer_physical_schemas: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """Layer -> physical schema (dataset) name."""
    naming_rules: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    date_dim_settings: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    set_up_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""
