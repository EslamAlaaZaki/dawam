"""add user management: users' is_active and must_change_password columns.

Revision ID: 4f1d8c2b9a6e
Revises: e2dda06a7a2d
Create Date: 2026-10-04 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4f1d8c2b9a6e"
down_revision: str | Sequence[str] | None = "e2dda06a7a2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_active")
