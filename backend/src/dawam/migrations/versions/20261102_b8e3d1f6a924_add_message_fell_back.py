"""Remember when a fallback model wrote an assistant answer (assistant module).

Revision ID: b8e3d1f6a924
Revises: f4a9c2d7e815
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e3d1f6a924"
down_revision: str | Sequence[str] | None = "f4a9c2d7e815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_messages",
        sa.Column("fell_back", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("assistant_messages", "fell_back")
