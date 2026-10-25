"""Add the Workspace's chunking strategy for document indexing.

Revision ID: a7d3f9b1c405
Revises: a7c3e9f1b204
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f9b1c405"
down_revision: str | Sequence[str] | None = "a7c3e9f1b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing Workspaces keep the heading-based chunking they were indexed with.
    op.add_column(
        "llm_workspace_settings",
        sa.Column("chunking_strategy", sa.String(16), nullable=False, server_default="headings"),
    )
    op.create_check_constraint(
        op.f("ck_llm_workspace_settings_chunking_strategy"),
        "llm_workspace_settings",
        "chunking_strategy IN ('headings', 'recursive', 'semantic')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_llm_workspace_settings_chunking_strategy"),
        "llm_workspace_settings",
        type_="check",
    )
    op.drop_column("llm_workspace_settings", "chunking_strategy")
