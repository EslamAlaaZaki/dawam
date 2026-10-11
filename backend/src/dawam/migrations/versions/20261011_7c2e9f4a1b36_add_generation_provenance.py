"""add generation provenance: generation keys and last generated values on DW tables and
columns, and the name of a deleted generated object on its tombstone (warehouse module).

Revision ID: 7c2e9f4a1b36
Revises: 7c4e2a9f1b63
Create Date: 2026-10-11 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c2e9f4a1b36"
down_revision: str | Sequence[str] | None = "7c4e2a9f1b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("dw_tables", "dw_columns"):
        op.add_column(table, sa.Column("generation_key", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("generated", sa.JSON(), nullable=True))
    op.add_column("tombstones", sa.Column("name", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("tombstones", "name")
    for table in ("dw_columns", "dw_tables"):
        op.drop_column(table, "generated")
        op.drop_column(table, "generation_key")
