"""Add the per-file index status (the ``index_document`` job's progress) to workspace files.

Revision ID: f1b6c3a8d902
Revises: e5a8c2d7f914
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1b6c3a8d902"
down_revision: str | Sequence[str] | None = "e5a8c2d7f914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Files that exist were indexed synchronously at upload: they are already ``indexed``.
    op.add_column(
        "workspace_files",
        sa.Column("index_status", sa.String(16), nullable=False, server_default="indexed"),
    )
    op.add_column("workspace_files", sa.Column("index_error", sa.Text()))
    op.create_check_constraint(
        op.f("ck_workspace_files_index_status"),
        "workspace_files",
        "index_status IN ('queued', 'extracting', 'ocr', 'chunking', 'embedding', "
        "'indexed', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_workspace_files_index_status"), "workspace_files", type_="check")
    op.drop_column("workspace_files", "index_error")
    op.drop_column("workspace_files", "index_status")
