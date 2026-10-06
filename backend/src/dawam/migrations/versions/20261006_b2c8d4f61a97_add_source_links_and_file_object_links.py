"""add source links and file object links: the files module's link tables.

Revision ID: b2c8d4f61a97
Revises: a1f4c7e92b35
Create Date: 2026-10-06 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c8d4f61a97"
down_revision: str | Sequence[str] | None = "a1f4c7e92b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('repo', 'jira', 'confluence', 'other')",
            name=op.f("ck_source_links_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_source_links_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_source_links_source_system_id_source_systems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_links")),
    )
    op.create_index(
        "ix_source_links_system_title", "source_links", ["source_system_id", "title", "id"]
    )
    op.create_table(
        "file_object_links",
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('table', 'column')", name=op.f("ck_file_object_links_object_type")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_file_object_links_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["workspace_files.id"],
            name=op.f("fk_file_object_links_file_id_workspace_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "file_id", "object_type", "object_id", name=op.f("pk_file_object_links")
        ),
    )
    op.create_index(
        "ix_file_object_links_object", "file_object_links", ["object_type", "object_id"]
    )


def downgrade() -> None:
    op.drop_table("file_object_links")
    op.drop_table("source_links")
