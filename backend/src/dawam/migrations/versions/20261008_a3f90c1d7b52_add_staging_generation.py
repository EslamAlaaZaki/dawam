"""add staging generation.

Revision ID: a3f90c1d7b52
Revises: 712926c5eed8
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f90c1d7b52"
down_revision: str | Sequence[str] | None = "712926c5eed8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "src_tables",
        sa.Column(
            "include_view_in_staging", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column("src_tables", sa.Column("placeholder_no", sa.Integer(), nullable=True))
    op.add_column("src_columns", sa.Column("placeholder_no", sa.Integer(), nullable=True))
    op.add_column("dw_tables", sa.Column("source_table_id", sa.Uuid(), nullable=True))
    op.add_column(
        "dw_tables",
        sa.Column("review_flags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.create_index(op.f("ix_dw_tables_source_table_id"), "dw_tables", ["source_table_id"])
    op.create_index(
        "uq_dw_tables_staging_source_table",
        "dw_tables",
        ["data_warehouse_id", "source_table_id"],
        unique=True,
        postgresql_where=sa.text("layer = 'staging'"),
    )
    op.add_column("dw_columns", sa.Column("source_column_id", sa.Uuid(), nullable=True))
    op.add_column(
        "dw_columns",
        sa.Column("review_flags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )
    op.create_index(op.f("ix_dw_columns_source_column_id"), "dw_columns", ["source_column_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_dw_columns_source_column_id"), table_name="dw_columns")
    op.drop_column("dw_columns", "review_flags")
    op.drop_column("dw_columns", "source_column_id")
    op.drop_index("uq_dw_tables_staging_source_table", table_name="dw_tables")
    op.drop_index(op.f("ix_dw_tables_source_table_id"), table_name="dw_tables")
    op.drop_column("dw_tables", "review_flags")
    op.drop_column("dw_tables", "source_table_id")
    op.drop_column("src_columns", "placeholder_no")
    op.drop_column("src_tables", "placeholder_no")
    op.drop_column("src_tables", "include_view_in_staging")
