"""add column profiles and the per-table top-N switch.

Revision ID: d6e3f9a4b725
Revises: c5d2e8f3a614
Create Date: 2026-10-07 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e3f9a4b725"
down_revision: str | Sequence[str] | None = "c5d2e8f3a614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "src_tables",
        sa.Column("top_n_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "column_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("src_column_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("row_cap", sa.BigInteger(), nullable=False),
        sa.Column("null_pct", sa.Float(), nullable=False),
        sa.Column("distinct_count", sa.BigInteger(), nullable=True),
        sa.Column("min", sa.Text(), nullable=True),
        sa.Column("max", sa.Text(), nullable=True),
        sa.Column("avg_len", sa.Float(), nullable=True),
        sa.Column("max_len", sa.BigInteger(), nullable=True),
        sa.Column("top_values", sa.JSON(), nullable=True),
        sa.Column("patterns", sa.ARRAY(sa.String(length=32)), server_default="{}", nullable=False),
        sa.Column("profiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_column_profiles_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["src_column_id"],
            ["src_columns.id"],
            name=op.f("fk_column_profiles_src_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_profiles")),
        sa.UniqueConstraint("src_column_id", name=op.f("uq_column_profiles_src_column_id")),
    )


def downgrade() -> None:
    op.drop_table("column_profiles")
    op.drop_column("src_tables", "top_n_enabled")
