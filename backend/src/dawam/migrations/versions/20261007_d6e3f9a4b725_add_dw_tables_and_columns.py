"""add the DW Schema's tables and columns (the Core and Mart model editor).

Revision ID: d6e3f9a4b725
Revises: c5d2e8f3a614
Create Date: 2026-10-07 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e3f9a4b725"
down_revision: str | Sequence[str] | None = "c5d2e8f3a614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = (
    "'sk', 'nk', 'fk', 'measure', 'attribute', 'degenerate_dimension', 'audit', "
    "'scd_valid_from', 'scd_valid_to', 'scd_current_flag', 'row_hash'"
)


def upgrade() -> None:
    op.create_table(
        "dw_tables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("fact_type", sa.String(length=24), nullable=True),
        sa.Column("grain", sa.String(length=1000), nullable=True),
        sa.Column("is_aggregate", sa.Boolean(), nullable=False),
        sa.Column("scd_type", sa.SmallInteger(), nullable=True),
        sa.Column("is_conformed", sa.Boolean(), nullable=False),
        sa.Column("unknown_member", sa.JSON(), nullable=True),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("layer IN ('staging', 'core', 'mart')", name=op.f("ck_dw_tables_layer")),
        sa.CheckConstraint(
            "kind IN ('staging', 'fact', 'dimension', 'bridge', 'generated', 'other')",
            name=op.f("ck_dw_tables_kind"),
        ),
        sa.CheckConstraint(
            "fact_type IS NULL OR fact_type IN "
            "('transactional', 'periodic_snapshot', 'accumulating_snapshot', 'factless')",
            name=op.f("ck_dw_tables_fact_type"),
        ),
        sa.CheckConstraint(
            "scd_type IS NULL OR scd_type IN (0, 1, 2)", name=op.f("ck_dw_tables_scd_type")
        ),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_dw_tables_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_dw_tables_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_tables")),
    )
    op.create_index(
        op.f("ix_dw_tables_data_warehouse_id"), "dw_tables", ["data_warehouse_id"], unique=False
    )
    op.create_index(
        "uq_dw_tables_layer_name",
        "dw_tables",
        ["data_warehouse_id", "layer", sa.text("lower(name)")],
        unique=True,
    )
    op.create_table(
        "dw_columns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.JSON(), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("additivity", sa.String(length=16), nullable=True),
        sa.Column("scd_type_override", sa.SmallInteger(), nullable=True),
        sa.Column("references_table_id", sa.Uuid(), nullable=True),
        sa.Column("role_name", sa.String(length=128), nullable=True),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("semantic_type", sa.String(length=64), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"role IN ({ROLES})", name=op.f("ck_dw_columns_role")),
        sa.CheckConstraint(
            "additivity IS NULL OR additivity IN ('additive', 'semi_additive', 'non_additive')",
            name=op.f("ck_dw_columns_additivity"),
        ),
        sa.CheckConstraint(
            "scd_type_override IS NULL OR scd_type_override IN (0, 1, 2)",
            name=op.f("ck_dw_columns_scd_type_override"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["dw_tables.id"],
            name=op.f("fk_dw_columns_table_id_dw_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["references_table_id"],
            ["dw_tables.id"],
            name=op.f("fk_dw_columns_references_table_id_dw_tables"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_columns")),
    )
    op.create_index(op.f("ix_dw_columns_table_id"), "dw_columns", ["table_id"], unique=False)
    op.create_index(
        op.f("ix_dw_columns_references_table_id"),
        "dw_columns",
        ["references_table_id"],
        unique=False,
    )
    op.create_index(
        "uq_dw_columns_table_name",
        "dw_columns",
        ["table_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("dw_columns")
    op.drop_table("dw_tables")
