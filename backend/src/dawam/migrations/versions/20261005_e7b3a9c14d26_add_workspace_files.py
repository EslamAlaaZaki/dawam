"""add workspace files: the files module's workspace_files table.

Revision ID: e7b3a9c14d26
Revises: 5b9d2e7a4c13
Create Date: 2026-10-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3a9c14d26"
down_revision: str | Sequence[str] | None = "5b9d2e7a4c13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_kind", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("text_status", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "owner_kind IN ('source_system', 'data_warehouse')",
            name=op.f("ck_workspace_files_owner_kind"),
        ),
        sa.CheckConstraint(
            "kind IN ('generated', 'uploaded')", name=op.f("ck_workspace_files_kind")
        ),
        sa.CheckConstraint(
            "text_status IN ('none', 'extracted', 'no_text_found')",
            name=op.f("ck_workspace_files_text_status"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_workspace_files_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_files_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_files")),
        sa.UniqueConstraint(
            "workspace_id",
            "owner_kind",
            "owner_id",
            "path",
            name=op.f("uq_workspace_files_workspace_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("workspace_files")
