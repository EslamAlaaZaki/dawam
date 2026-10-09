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
STAGING_SOURCE_UNIQUE = "uq_dw_tables_staging_source_table"
"""Violated by a second Staging Table for one Source Table."""
TOMBSTONE_SOURCE_UNIQUE = "uq_tombstones_staging_source"
"""Violated by a second Tombstone for one Source Table."""
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
OBJECT_STATUSES = ("present", "source_removed", "deleted")
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
        sa.Index(
            STAGING_SOURCE_UNIQUE,
            "data_warehouse_id",
            "source_table_id",
            unique=True,
            postgresql_where=sa.text("layer = 'staging'"),
        ),
        sa.CheckConstraint(f"layer IN ({_in(LAYERS)})", name="layer"),
        sa.CheckConstraint(f"kind IN ({_in(TABLE_KINDS)})", name="kind"),
        sa.CheckConstraint(
            f"fact_type IS NULL OR fact_type IN ({_in(FACT_TYPES)})", name="fact_type"
        ),
        sa.CheckConstraint("scd_type IS NULL OR scd_type IN (0, 1, 2)", name="scd_type"),
        sa.CheckConstraint(f"status IN ({_in(OBJECT_STATUSES)})", name="status"),
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
    source_table_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """A Staging Table's Source Object (``src_tables.id``, a plain id: that table is the
    sources module's); at most one Staging Table per Source Table."""
    review_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        sa.JSON, default=list, server_default=sa.text("'[]'")
    )
    """What generation flagged for review: ``[{"code", "message"}]`` (spec §6.7)."""
    status: Mapped[str] = mapped_column(sa.String(16), default="present", server_default="present")
    """``source_removed``: a Staging Table whose source is gone; kept, flagged and scored."""
    edited_fields: Mapped[list[str]] = mapped_column(
        sa.JSON, default=list, server_default=sa.text("'[]'")
    )
    """The fields a user overrode on a Staging Table; sync reports a change to one as a
    conflict instead of overwriting it (spec §6.9)."""
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
        sa.CheckConstraint(f"status IN ({_in(OBJECT_STATUSES)})", name="status"),
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
    source_column_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """A staging column's Source Object (``src_columns.id``)."""
    review_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        sa.JSON, default=list, server_default=sa.text("'[]'")
    )
    """What generation flagged for review: ``[{"code", "message"}]``."""
    status: Mapped[str] = mapped_column(sa.String(16), default="present", server_default="present")
    """``source_removed``: a staging column whose source column is gone."""
    edited_fields: Mapped[list[str]] = mapped_column(
        sa.JSON, default=list, server_default=sa.text("'[]'")
    )
    """The fields a user overrode on a staging column (``name``, ``data_type``,
    ``is_nullable``)."""
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()


MAPPING_TYPES = ("direct", "derived", "constant", "lookup", "system", "not_in_branch", "unmapped")
BRANCH_NAME_MAX_LENGTH = 128
EDGE_KINDS = ("value", "uses", "lookup", "kpi")
EDGE_NODE_TYPES = ("src_column", "dw_column", "dw_table", "branch", "kpi")
MAPPING_TEXT_MAX_LENGTH = 4000


class TableMappingRecord(Base):
    """How a Core or Mart table is filled from the Layer below (spec §6.14 ``TableMapping``)."""

    __tablename__ = "table_mappings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dw_table_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("dw_tables.id", ondelete="CASCADE"), unique=True
    )
    integration_rule: Mapped[str | None] = mapped_column(sa.String(MAPPING_TEXT_MAX_LENGTH))
    match_keys: Mapped[list[str]] = mapped_column(sa.JSON)
    notes: Mapped[str] = mapped_column(sa.String(MAPPING_TEXT_MAX_LENGTH), default="")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()


class MappingBranchRecord(Base):
    """One row-set of a table, combined with the others by UNION ALL (spec §6.14)."""

    __tablename__ = "mapping_branches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    table_mapping_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("table_mappings.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(sa.String(BRANCH_NAME_MAX_LENGTH))
    driving_input: Mapped[str] = mapped_column(sa.Text)
    """The branch's driving table, in the target dialect (a table of the Layer below)."""
    joins: Mapped[str] = mapped_column(sa.Text, default="")
    filters: Mapped[str] = mapped_column(sa.Text, default="")
    group_by: Mapped[str | None] = mapped_column(sa.Text)
    having: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()


COLUMN_MAPPING_UNIQUE = "uq_column_mappings_column_branch"
TABLE_LEVEL_MAPPING_UNIQUE = "uq_column_mappings_table_level"


class ColumnMappingRecord(Base):
    """One target column's mapping from the Layer below (spec §6.14 ``ColumnMapping``);
    per branch, or table-level when ``branch_id`` is null."""

    __tablename__ = "column_mappings"
    __table_args__ = (
        sa.CheckConstraint(f"mapping_type IN ({_in(MAPPING_TYPES)})", name="mapping_type"),
        sa.Index(COLUMN_MAPPING_UNIQUE, "dw_column_id", "branch_id", unique=True),
        sa.Index(
            TABLE_LEVEL_MAPPING_UNIQUE,
            "dw_column_id",
            unique=True,
            postgresql_where=sa.text("branch_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    table_mapping_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("table_mappings.id", ondelete="CASCADE"), index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("mapping_branches.id", ondelete="CASCADE"), index=True
    )
    dw_column_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("dw_columns.id", ondelete="CASCADE")
    )
    mapping_type: Mapped[str] = mapped_column(sa.String(16))
    rule_text: Mapped[str] = mapped_column(sa.String(MAPPING_TEXT_MAX_LENGTH), default="")
    sql_expression: Mapped[str] = mapped_column(sa.Text, default="")
    """In the target platform's dialect; the master of the mapping's inputs."""
    lookup: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """A ``lookup`` mapping's ``{"nk_inputs": [table.column], "as_of_input"?, "unknown_key"}``;
    the dimension is the column's ``references_table_id``, not stored twice."""
    validation: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """``{"unparsed": bool, "errors": [{"code", "message"}]}``."""
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()


class LineageEdgeRecord(Base):
    """An edge of the lineage graph, derived from a mapping's SQL on every save (§6.14)."""

    __tablename__ = "lineage_edges"
    __table_args__ = (
        sa.CheckConstraint(f"kind IN ({_in(EDGE_KINDS)})", name="kind"),
        sa.CheckConstraint(f"from_type IN ({_in(EDGE_NODE_TYPES)})", name="from_type"),
        sa.CheckConstraint(f"to_type IN ({_in(EDGE_NODE_TYPES)})", name="to_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(sa.String(8))
    from_type: Mapped[str] = mapped_column(sa.String(16))
    from_id: Mapped[uuid.UUID] = mapped_column(index=True)
    to_type: Mapped[str] = mapped_column(sa.String(16))
    to_id: Mapped[uuid.UUID] = mapped_column(index=True)
    mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("column_mappings.id", ondelete="CASCADE"), index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("mapping_branches.id", ondelete="CASCADE"), index=True
    )
    """Set on the ``uses`` edges of a branch's own joins, filters, GROUP BY and HAVING."""


class TombstoneRecord(Base):
    """A deleted Staging Table (or generated object) that sync and regeneration never
    propose again (spec §6.9 ``Tombstone``)."""

    __tablename__ = "tombstones"
    __table_args__ = (
        sa.Index(
            TOMBSTONE_SOURCE_UNIQUE,
            "data_warehouse_id",
            "src_object_id",
            unique=True,
            postgresql_where=sa.text("object_type = 'staging_table'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    data_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_warehouses.id", ondelete="CASCADE"), index=True
    )
    object_type: Mapped[str] = mapped_column(sa.String(16))
    """``staging_table`` for now."""
    generation_key: Mapped[str | None] = mapped_column(sa.String(128))
    src_object_id: Mapped[uuid.UUID | None] = mapped_column()
    """The Staging Table's Source Table (``src_tables.id``)."""
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class ScoreRunRecord(Base):
    """One summary row per scoring run, for the trend (spec §6.11 ``ScoreRun``)."""

    __tablename__ = "score_runs"
    __table_args__ = (sa.Index("ix_score_runs_dw_created_at", "data_warehouse_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    data_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_warehouses.id", ondelete="CASCADE")
    )
    score: Mapped[float | None] = mapped_column(sa.Float)
    """``None``: nothing in Core or Mart to score."""
    grade: Mapped[str | None] = mapped_column(sa.String(1))
    per_layer: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    """Layer -> ``{"score": float | None, "grade": str | None, "tables": int}``."""
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class ScoreCheckResultRecord(Base):
    """The latest run's result of one check on one object; replaced on every run."""

    __tablename__ = "score_check_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    data_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("data_warehouses.id", ondelete="CASCADE"), index=True
    )
    check_code: Mapped[str] = mapped_column(sa.String(64))
    severity: Mapped[str] = mapped_column(sa.String(8))
    layer: Mapped[str] = mapped_column(sa.String(16))
    object_type: Mapped[str] = mapped_column(sa.String(16))
    """``table`` or ``column``."""
    object_id: Mapped[uuid.UUID] = mapped_column()
    table_id: Mapped[uuid.UUID] = mapped_column()
    """The table the object is, or belongs to."""
    object_name: Mapped[str] = mapped_column(sa.String(300))
    passed: Mapped[bool] = mapped_column()
    message: Mapped[str] = mapped_column(sa.String(1000), default="")
