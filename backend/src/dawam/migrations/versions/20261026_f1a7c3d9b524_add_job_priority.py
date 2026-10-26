"""Give jobs a priority: chat-started jobs run before re-index jobs.

Revision ID: f1a7c3d9b524
Revises: c8f2a4d6e913
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a7c3d9b524"
down_revision: str | Sequence[str] | None = "c8f2a4d6e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.create_index(
        "ix_jobs_status_priority_created_at",
        "jobs",
        ["status", sa.text("priority DESC"), "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_priority_created_at", table_name="jobs")
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])
    op.drop_column("jobs", "priority")
