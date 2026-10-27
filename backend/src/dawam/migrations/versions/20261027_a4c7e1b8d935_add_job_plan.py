"""Let jobs keep a plan (todo list) shown with them (#403).

Revision ID: a4c7e1b8d935
Revises: d6a3f8b1c472
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a4c7e1b8d935"
down_revision: str | Sequence[str] | None = "d6a3f8b1c472"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("plan", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "plan")
