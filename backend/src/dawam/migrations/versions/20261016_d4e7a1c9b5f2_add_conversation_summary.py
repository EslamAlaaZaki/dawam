"""Keep a summary of the compacted turns of a conversation.

Revision ID: d4e7a1c9b5f2
Revises: d4e7a1b9c258
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a1c9b5f2"
down_revision: str | Sequence[str] | None = "d4e7a1b9c258"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "assistant_conversations", sa.Column("summary_through", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assistant_conversations", "summary_through")
    op.drop_column("assistant_conversations", "summary")
