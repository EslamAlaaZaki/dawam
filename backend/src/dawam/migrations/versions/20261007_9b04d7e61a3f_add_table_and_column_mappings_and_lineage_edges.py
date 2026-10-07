"""add table mappings, column mappings and lineage edges.

Revision ID: 9b04d7e61a3f
Revises: e7a1c4d9f2b8
Create Date: 2026-10-07 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b04d7e61a3f"
down_revision: str | Sequence[str] | None = "e7a1c4d9f2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NODE_TYPES = "'src_column', 'dw_column', 'dw_table', 'branch', 'kpi'"


def upgrade() -> None:
    op.create_table(
        "table_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dw_table_id", sa.Uuid(), nullable=False),
        sa.Column("integration_rule", sa.String(length=4000), nullable=True),
        sa.Column("match_keys", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dw_table_id"],
            ["dw_tables.id"],
            name=op.f("fk_table_mappings_dw_table_id_dw_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_table_mappings")),
        sa.UniqueConstraint("dw_table_id", name=op.f("uq_table_mappings_dw_table_id")),
    )
    op.create_table(
        "column_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_mapping_id", sa.Uuid(), nullable=False),
        sa.Column("dw_column_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_type", sa.String(length=16), nullable=False),
        sa.Column("rule_text", sa.String(length=4000), nullable=False),
        sa.Column("sql_expression", sa.Text(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "mapping_type IN ('direct', 'derived', 'constant', 'unmapped')",
            name=op.f("ck_column_mappings_mapping_type"),
        ),
        sa.ForeignKeyConstraint(
            ["table_mapping_id"],
            ["table_mappings.id"],
            name=op.f("fk_column_mappings_table_mapping_id_table_mappings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dw_column_id"],
            ["dw_columns.id"],
            name=op.f("fk_column_mappings_dw_column_id_dw_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_column_mappings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_mappings")),
        sa.UniqueConstraint("dw_column_id", name=op.f("uq_column_mappings_dw_column_id")),
    )
    op.create_index(
        op.f("ix_column_mappings_table_mapping_id"),
        "column_mappings",
        ["table_mapping_id"],
        unique=False,
    )
    op.create_table(
        "lineage_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("from_type", sa.String(length=16), nullable=False),
        sa.Column("from_id", sa.Uuid(), nullable=False),
        sa.Column("to_type", sa.String(length=16), nullable=False),
        sa.Column("to_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('value', 'uses', 'lookup', 'kpi')", name=op.f("ck_lineage_edges_kind")
        ),
        sa.CheckConstraint(f"from_type IN ({NODE_TYPES})", name=op.f("ck_lineage_edges_from_type")),
        sa.CheckConstraint(f"to_type IN ({NODE_TYPES})", name=op.f("ck_lineage_edges_to_type")),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["column_mappings.id"],
            name=op.f("fk_lineage_edges_mapping_id_column_mappings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lineage_edges")),
    )
    op.create_index(op.f("ix_lineage_edges_from_id"), "lineage_edges", ["from_id"], unique=False)
    op.create_index(op.f("ix_lineage_edges_to_id"), "lineage_edges", ["to_id"], unique=False)
    op.create_index(
        op.f("ix_lineage_edges_mapping_id"), "lineage_edges", ["mapping_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("lineage_edges")
    op.drop_table("column_mappings")
    op.drop_table("table_mappings")
