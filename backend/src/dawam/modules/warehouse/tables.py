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


TABLE_NAME_UNIQUE = "uq_dw_tables_layer_name"
"""Violated by a table name (any case) another table of the same Layer already has."""
COLUMN_NAME_UNIQUE = "uq_dw_columns_table_name"
"""Violated by a column name (any case) another column of the same table already has."""

LAYERS = ("staging", "core", "mart")
TABLE_KINDS = ("staging", "fact", "dimension", "bridge", "generated", "other")
FACT_TYPES = ("transactional", "periodic_snapshot", "accumulating_snapshot", "factless")
COLUMN_ROLES = (
    "sk",
    "nk",
    "fk",
    "measure",
    "attribute",
    "degenerate_dimension",
    "audit",
    "scd_valid_from",
    "scd_valid_to",
    "scd_current_flag",
    "row_hash",
)
ADDITIVITIES = ("additive", "semi_additive", "non_additive")
DESCRIPTION_MAX_LENGTH = 4000
GRAIN_MAX_LENGTH = 1000
NAME_MAX_LENGTH = 128


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class DwTableRecord(Base):
    """A table of the DW Schema (spec §6.9 ``DwTable``)."""

    __tablename__ = "dw_tables"
    __table_args__ = (
        sa.Index(
            TABLE_NAME_UNIQUE, "data_warehouse_id", "layer", sa.text("lower(name)"), unique=True
        ),
        sa.CheckConstraint(f"layer IN ({_in(LAYERS)})", name="layer"),
        sa.CheckConstraint(f"kind IN ({_in(TABLE_KINDS)})", name="kind"),
        sa.CheckConstraint(
            f"fact_type IS NULL OR fact_type IN ({_in(FACT_TYPES)})", name="fact_type"
        ),
        sa.CheckConstraint("scd_type IS NULL OR scd_type IN (0, 1, 2)", name="scd_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    data_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_warehouses.id", ondelete="CASCADE"), index=True
    )
    layer: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    kind: Mapped[str] = mapped_column(sa.String(16))
    fact_type: Mapped[str | None] = mapped_column(sa.String(24))
    grain: Mapped[str | None] = mapped_column(sa.String(GRAIN_MAX_LENGTH))
    is_aggregate: Mapped[bool] = mapped_column(default=False)
    scd_type: Mapped[int | None] = mapped_column(sa.SmallInteger)
    is_conformed: Mapped[bool] = mapped_column(default=False)
    unknown_member: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """A dimension's unknown member: ``{"surrogate_key": -1, "defaults": {column: value}}``."""
    description: Mapped[str] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH), default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""


class DwColumnRecord(Base):
    """A column of a DW Schema table (spec §6.9 ``DwColumn``)."""

    __tablename__ = "dw_columns"
    __table_args__ = (
        sa.Index(COLUMN_NAME_UNIQUE, "table_id", sa.text("lower(name)"), unique=True),
        sa.CheckConstraint(f"role IN ({_in(COLUMN_ROLES)})", name="role"),
        sa.CheckConstraint(
            f"additivity IS NULL OR additivity IN ({_in(ADDITIVITIES)})", name="additivity"
        ),
        sa.CheckConstraint(
            "scd_type_override IS NULL OR scd_type_override IN (0, 1, 2)", name="scd_type_override"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("dw_tables.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    ordinal: Mapped[int] = mapped_column()
    data_type: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """The neutral type: ``{"type", "length", "precision", "scale"}``."""
    is_nullable: Mapped[bool] = mapped_column()
    role: Mapped[str] = mapped_column(sa.String(24))
    additivity: Mapped[str | None] = mapped_column(sa.String(16))
    scd_type_override: Mapped[int | None] = mapped_column(sa.SmallInteger)
    references_table_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("dw_tables.id"), index=True
    )
    """No ``ondelete``: the check runs at the end of the statement, so deleting a whole
    Workspace (which cascades to every table) still works."""
    role_name: Mapped[str | None] = mapped_column(sa.String(NAME_MAX_LENGTH))
    description: Mapped[str] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH), default="")
    semantic_type: Mapped[str | None] = mapped_column(sa.String(64))
    is_system: Mapped[bool] = mapped_column(default=False)
    """DAWAM maintains it (SCD2 housekeeping): not edited or deleted by hand."""
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
