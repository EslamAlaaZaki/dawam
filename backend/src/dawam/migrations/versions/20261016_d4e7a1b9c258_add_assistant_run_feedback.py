"""Add member feedback on assistant answers.

Revision ID: d4e7a1b9c258
Revises: a1c5e8d2b7f3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a1b9c258"
down_revision: str | Sequence[str] | None = "a1c5e8d2b7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_run_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating IN ('up', 'down')", name=op.f("ck_assistant_run_feedback_rating")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_runs.id"],
            name=op.f("fk_assistant_run_feedback_run_id_assistant_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_assistant_run_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_run_feedback")),
        sa.UniqueConstraint("run_id", "user_id", name=op.f("uq_assistant_run_feedback_run_id")),
    )


def downgrade() -> None:
    op.drop_table("assistant_run_feedback")
