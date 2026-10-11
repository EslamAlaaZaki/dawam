"""add kpi suggestion dismissals: rule-based suggestions a user hid (kpis module).

Revision ID: 9a3d5e7c1b28
Revises: 9d3f6b2e8a41
Create Date: 2026-10-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a3d5e7c1b28"
down_revision: str | Sequence[str] | None = "9d3f6b2e8a41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kpi_suggestion_dismissals",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("dismissed_by", sa.Uuid(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dismissed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workspace_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("kpi_suggestion_dismissals")
