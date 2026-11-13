"""A KPI keeps the key of the rule-based suggestion it was accepted from, so removing it
keeps the suggestion dismissed (#598).

Revision ID: c5f2a8d3b714
Revises: b3e8d1f4a926
Create Date: 2026-11-13 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5f2a8d3b714"
down_revision: str | Sequence[str] | None = "b3e8d1f4a926"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kpis", sa.Column("suggestion_key", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("kpis", "suggestion_key")
