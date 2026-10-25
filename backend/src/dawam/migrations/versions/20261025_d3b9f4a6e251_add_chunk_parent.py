"""Small-to-big retrieval: a passage knows the parent section it was cut from.

Revision ID: d3b9f4a6e251
Revises: a9d4e7b2c615
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b9f4a6e251"
down_revision: str | Sequence[str] | None = "a9d4e7b2c615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("parent_ordinal", sa.Integer()))
    op.add_column("document_chunks", sa.Column("parent_content", sa.Text()))


def downgrade() -> None:
    op.drop_column("document_chunks", "parent_content")
    op.drop_column("document_chunks", "parent_ordinal")
