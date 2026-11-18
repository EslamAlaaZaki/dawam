"""Documents whose passages were not (all) embedded say why (#622): ``workspace_files``
gets a nullable ``embedding_error``. A rebuild remembers whether it must extract again
(#573): ``document_embedding_rebuilds.extract``, so a newer rebuild that supersedes an
unfinished extract pass keeps it.

Revision ID: a3c8e1f5b927
Revises: e8b3f1a6c254
Create Date: 2026-11-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c8e1f5b927"
down_revision: str | Sequence[str] | None = "e8b3f1a6c254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workspace_files", sa.Column("embedding_error", sa.Text(), nullable=True))
    op.add_column(
        "document_embedding_rebuilds",
        sa.Column("extract", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("document_embedding_rebuilds", "extract")
    op.drop_column("workspace_files", "embedding_error")
