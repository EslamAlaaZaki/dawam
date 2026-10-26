"""Reasoning model settings, and the reasoning tokens of each call.

Revision ID: c4d8e1a6f203
Revises: b9e4d2a7c518
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e1a6f203"
down_revision: str | Sequence[str] | None = "b9e4d2a7c518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("reasoning_enabled", "reasoning_show"):
        op.add_column(
            "llm_models",
            sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    op.add_column("llm_models", sa.Column("reasoning_effort", sa.String(16), nullable=True))
    op.add_column("llm_models", sa.Column("reasoning_budget_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "llm_usage",
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("llm_usage", "reasoning_tokens")
    op.drop_column("llm_models", "reasoning_budget_tokens")
    op.drop_column("llm_models", "reasoning_effort")
    op.drop_column("llm_models", "reasoning_show")
    op.drop_column("llm_models", "reasoning_enabled")
