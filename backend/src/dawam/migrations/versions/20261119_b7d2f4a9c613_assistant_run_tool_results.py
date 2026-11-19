"""Store the full result of each tool call of an assistant run.

Revision ID: b7d2f4a9c613
Revises: a3c8e1f5b927
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2f4a9c613"
down_revision: str | Sequence[str] | None = "a3c8e1f5b927"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_run_tool_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=True),
        sa.Column("flagged", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("token_size", sa.Integer(), nullable=False),
        sa.Column("sent_mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "sent_mode IN ('full', 'digest_over_cap')",
            name=op.f("ck_assistant_run_tool_results_sent_mode"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_runs.id"],
            name=op.f("fk_assistant_run_tool_results_run_id_assistant_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_run_tool_results")),
        sa.UniqueConstraint(
            "run_id", "tool_call_id", name=op.f("uq_assistant_run_tool_results_run_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("assistant_run_tool_results")
