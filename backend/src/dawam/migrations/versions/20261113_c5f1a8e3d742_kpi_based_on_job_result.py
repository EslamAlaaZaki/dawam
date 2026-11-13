"""AI KPI suggestions per Source System: the source tables a KPI is based on, and the
outcome a job reports when it ends (#597).

Revision ID: c5f1a8e3d742
Revises: c5f2a8d3b714
Create Date: 2026-11-13 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5f1a8e3d742"
down_revision: str | Sequence[str] | None = "c5f2a8d3b714"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kpis",
        sa.Column(
            "based_on",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "jobs", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "result")
    op.drop_column("kpis", "based_on")
