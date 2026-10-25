"""add the contextual header of a document chunk (#369).

Revision ID: a9d4e7b2c615
Revises: a7c3e9f1b204
Create Date: 2026-10-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4e7b2c615"
down_revision: str | Sequence[str] | None = "a7c3e9f1b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("context_header", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "context_header")
