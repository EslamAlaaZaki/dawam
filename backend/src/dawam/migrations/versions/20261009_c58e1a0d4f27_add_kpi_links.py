"""add kpi links: the DW columns each KPI uses (kpis module).

Revision ID: c58e1a0d4f27
Revises: a3f90c1d7b52
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c58e1a0d4f27"
down_revision: str | Sequence[str] | None = "a3f90c1d7b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kpi_links",
        sa.Column("kpi_id", sa.Uuid(), nullable=False),
        sa.Column("dw_column_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["kpi_id"], ["kpis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dw_column_id"], ["dw_columns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("kpi_id", "dw_column_id"),
    )
    op.create_index(op.f("ix_kpi_links_dw_column_id"), "kpi_links", ["dw_column_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_kpi_links_dw_column_id"), table_name="kpi_links")
    op.drop_table("kpi_links")
