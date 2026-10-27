"""Add Workspace memory: the facts the team confirmed for the assistant (#413, ADR 0010).

Revision ID: e2b7c4a9f618
Revises: a4c7e1b9d305
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7c4a9f618"
down_revision: str | Sequence[str] | None = "a4c7e1b9d305"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memory_items_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_items")),
    )
    op.create_index(
        "ix_memory_items_workspace", "memory_items", ["workspace_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_memory_items_workspace", table_name="memory_items")
    op.drop_table("memory_items")
