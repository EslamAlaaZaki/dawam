"""remember the reasoning effort of each assistant conversation (assistant module).

Revision ID: c5d8e2a1f347
Revises: b8e3d1f6a924
Create Date: 2026-11-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d8e2a1f347"
down_revision: str | Sequence[str] | None = "b8e3d1f6a924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_conversations", sa.Column("last_effort", sa.String(), nullable=True))
    op.create_check_constraint(
        op.f("ck_assistant_conversations_last_effort"),
        "assistant_conversations",
        "last_effort IN ('low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_assistant_conversations_last_effort"), "assistant_conversations", type_="check"
    )
    op.drop_column("assistant_conversations", "last_effort")
