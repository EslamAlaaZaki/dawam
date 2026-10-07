"""add LLM model-role settings, token budgets and usage.

Revision ID: d6e3f9a4c725
Revises: d6e3f9a4b725
Create Date: 2026-10-07 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e3f9a4c725"
down_revision: str | Sequence[str] | None = "d6e3f9a4b725"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_model_id", sa.Uuid(), nullable=True),
        sa.Column("light_model_id", sa.Uuid(), nullable=True),
        sa.Column("embedding_model_id", sa.Uuid(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("monthly_token_budget", sa.BigInteger(), nullable=True),
        sa.Column("reindex_needed", sa.Boolean(), nullable=False),
        sa.Column("reindex_reason", sa.String(length=64), nullable=True),
        sa.Column("reindex_flagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_model_id"], ["llm_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["light_model_id"], ["llm_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["embedding_model_id"], ["llm_models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "llm_workspace_budgets",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("monthly_token_budget", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_table("llm_workspace_budgets")
    op.drop_table("llm_settings")
