"""relationships.confidence is nullable (a manually added relationship has none).

Revision ID: e4b7a1c9d352
Revises: d7a3f5b2c618
Create Date: 2026-11-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7a1c9d352"
down_revision: str | Sequence[str] | None = "d7a3f5b2c618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("relationships", "confidence", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM relationships WHERE confidence IS NULL")
    op.alter_column("relationships", "confidence", existing_type=sa.Float(), nullable=False)
