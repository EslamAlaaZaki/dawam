"""Record the prompt version a run was given.

Revision ID: d4e7a1b9c2f5
Revises: a1c5e8d2b7f3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a1b9c2f5"
down_revision: str | Sequence[str] | None = "a1c5e8d2b7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_runs", sa.Column("prompt_version", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_runs", "prompt_version")
