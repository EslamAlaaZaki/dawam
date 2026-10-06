"""add source enhancements: descriptions, tags, sensitivity and classification on Source Objects.

Revision ID: b3c8d5e1f704
Revises: a1f4c7e92b35
Create Date: 2026-10-06 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c8d5e1f704"
down_revision: str | Sequence[str] | None = "a1f4c7e92b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("src_tables", "src_columns"):
        op.add_column(table, sa.Column("description", sa.String(length=4000), nullable=True))
        op.add_column(
            table,
            sa.Column("tags", sa.ARRAY(sa.String(length=40)), server_default="{}", nullable=False),
        )
        op.add_column(
            table,
            sa.Column("is_sensitive", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    op.add_column("src_tables", sa.Column("classification", sa.String(length=16), nullable=True))
    op.add_column("src_tables", sa.Column("scd_hint", sa.String(length=200), nullable=True))
    op.create_check_constraint(
        op.f("ck_src_tables_classification"),
        "src_tables",
        "classification IS NULL OR classification IN "
        "('master', 'transactional', 'reference', 'log', 'landing')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_src_tables_classification"), "src_tables", type_="check")
    op.drop_column("src_tables", "scd_hint")
    op.drop_column("src_tables", "classification")
    for table in ("src_columns", "src_tables"):
        op.drop_column(table, "is_sensitive")
        op.drop_column(table, "tags")
        op.drop_column(table, "description")
