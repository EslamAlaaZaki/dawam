"""add mapping branches; column mappings belong to a branch or the table.

Revision ID: c27e5a91d4b6
Revises: 9b04d7e61a3f
Create Date: 2026-10-07 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c27e5a91d4b6"
down_revision: str | Sequence[str] | None = "9b04d7e61a3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "'direct', 'derived', 'constant', 'unmapped'"
NEW_TYPES = "'direct', 'derived', 'constant', 'not_in_branch', 'unmapped'"


def upgrade() -> None:
    op.create_table(
        "mapping_branches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_mapping_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("driving_input", sa.Text(), nullable=False),
        sa.Column("joins", sa.Text(), nullable=False),
        sa.Column("filters", sa.Text(), nullable=False),
        sa.Column("group_by", sa.Text(), nullable=True),
        sa.Column("having", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["table_mapping_id"],
            ["table_mappings.id"],
            name=op.f("fk_mapping_branches_table_mapping_id_table_mappings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_branches")),
    )
    op.create_index(
        op.f("ix_mapping_branches_table_mapping_id"),
        "mapping_branches",
        ["table_mapping_id"],
        unique=False,
    )
    op.add_column("column_mappings", sa.Column("branch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_column_mappings_branch_id_mapping_branches"),
        "column_mappings",
        "mapping_branches",
        ["branch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_column_mappings_branch_id"), "column_mappings", ["branch_id"], unique=False
    )
    op.add_column("lineage_edges", sa.Column("branch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_lineage_edges_branch_id_mapping_branches"),
        "lineage_edges",
        "mapping_branches",
        ["branch_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix_lineage_edges_branch_id"), "lineage_edges", ["branch_id"], unique=False
    )
    op.drop_constraint(op.f("uq_column_mappings_dw_column_id"), "column_mappings", type_="unique")
    op.create_index(
        "uq_column_mappings_column_branch",
        "column_mappings",
        ["dw_column_id", "branch_id"],
        unique=True,
    )
    op.create_index(
        "uq_column_mappings_table_level",
        "column_mappings",
        ["dw_column_id"],
        unique=True,
        postgresql_where=sa.text("branch_id IS NULL"),
    )
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({NEW_TYPES})",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lineage_edges_branch_id"), table_name="lineage_edges")
    op.drop_constraint(
        op.f("fk_lineage_edges_branch_id_mapping_branches"), "lineage_edges", type_="foreignkey"
    )
    op.drop_column("lineage_edges", "branch_id")
    op.execute("DELETE FROM column_mappings WHERE branch_id IS NOT NULL")
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({OLD_TYPES})",
    )
    op.drop_index("uq_column_mappings_table_level", table_name="column_mappings")
    op.drop_index("uq_column_mappings_column_branch", table_name="column_mappings")
    op.create_unique_constraint(
        op.f("uq_column_mappings_dw_column_id"), "column_mappings", ["dw_column_id"]
    )
    op.drop_index(op.f("ix_column_mappings_branch_id"), table_name="column_mappings")
    op.drop_constraint(
        op.f("fk_column_mappings_branch_id_mapping_branches"), "column_mappings", type_="foreignkey"
    )
    op.drop_column("column_mappings", "branch_id")
    op.drop_table("mapping_branches")
