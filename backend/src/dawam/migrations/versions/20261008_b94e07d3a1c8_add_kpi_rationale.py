"""add kpi rationale: why the AI suggested a KPI.

Revision ID: b94e07d3a1c8
Revises: a3f90c1d7b52
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b94e07d3a1c8"
down_revision: str | Sequence[str] | None = "a3f90c1d7b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kpis", sa.Column("rationale", sa.String(length=4000), nullable=True))


def downgrade() -> None:
    op.drop_column("kpis", "rationale")
