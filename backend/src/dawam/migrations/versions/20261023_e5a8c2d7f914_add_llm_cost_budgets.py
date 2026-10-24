"""Add monthly currency budgets (installation and per Workspace) beside the token budgets.

Revision ID: e5a8c2d7f914
Revises: b3d7f1a9c502
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a8c2d7f914"
down_revision: str | Sequence[str] | None = "b3d7f1a9c502"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_settings", sa.Column("monthly_cost_budget", sa.Numeric(18, 6)))
    op.add_column("llm_settings", sa.Column("cost_budget_currency", sa.String(3)))
    op.create_table(
        "llm_workspace_cost_budgets",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("monthly_cost_budget", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_workspace_cost_budgets")
    op.drop_column("llm_settings", "cost_budget_currency")
    op.drop_column("llm_settings", "monthly_cost_budget")
