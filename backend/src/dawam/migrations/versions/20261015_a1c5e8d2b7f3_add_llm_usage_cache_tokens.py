"""Record cached prompt tokens per model call.

Revision ID: a1c5e8d2b7f3
Revises: b6f2d9e4a713
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e8d2b7f3"
down_revision: str | Sequence[str] | None = "b6f2d9e4a713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("cache_read_tokens", "cache_write_tokens"):
        op.add_column(
            "llm_usage",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("llm_usage", "cache_write_tokens")
    op.drop_column("llm_usage", "cache_read_tokens")
