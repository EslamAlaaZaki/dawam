"""Soft-delete assistant conversations: their starter may delete them.

Revision ID: f4a9c2d7e815
Revises: e2b7c4a9f618
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a9c2d7e815"
down_revision: str | Sequence[str] | None = "e2b7c4a9f618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_conversations", "deleted_at")
