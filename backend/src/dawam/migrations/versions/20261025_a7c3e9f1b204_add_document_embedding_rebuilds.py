"""Add `document_embedding_rebuilds`: Workspaces whose documents are being embedded again
after their embedding model changed (ADR 0006); search matches text only meanwhile.

Revision ID: a7c3e9f1b204
Revises: c8e2a5f7d104
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f1b204"
down_revision: str | Sequence[str] | None = "c8e2a5f7d104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_embedding_rebuilds",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("rebuild_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_document_embedding_rebuilds_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_document_embedding_rebuilds")),
    )


def downgrade() -> None:
    op.drop_table("document_embedding_rebuilds")
