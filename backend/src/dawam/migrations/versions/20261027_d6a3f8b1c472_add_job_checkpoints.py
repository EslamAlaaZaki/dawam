"""Let jobs save a checkpoint to resume from when their worker dies (#401).

Revision ID: d6a3f8b1c472
Revises: b9e4d2a7c518
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d6a3f8b1c472"
down_revision: str | Sequence[str] | None = "b9e4d2a7c518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("checkpoint", JSONB(), nullable=True))
    op.add_column("jobs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("jobs", "attempts")
    op.drop_column("jobs", "checkpoint")
