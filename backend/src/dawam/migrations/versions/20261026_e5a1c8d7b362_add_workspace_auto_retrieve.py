"""Add the Workspace's auto-retrieve switch for the assistant's first message.

Revision ID: e5a1c8d7b362
Revises: d3b9f4a6e251
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1c8d7b362"
down_revision: str | Sequence[str] | None = "d3b9f4a6e251"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Off for every Workspace: nothing changes until an owner switches it on.
    op.add_column(
        "llm_workspace_settings",
        sa.Column("auto_retrieve", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("llm_workspace_settings", "auto_retrieve")
