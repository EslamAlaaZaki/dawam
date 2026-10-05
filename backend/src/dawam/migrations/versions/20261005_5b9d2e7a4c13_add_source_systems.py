"""add source systems: the sources module's source_systems table.

Revision ID: 5b9d2e7a4c13
Revises: d4a9e7b31c58
Create Date: 2026-10-05 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b9d2e7a4c13"
down_revision: str | Sequence[str] | None = "d4a9e7b31c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_systems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("business_owner", sa.String(length=200), nullable=False),
        sa.Column("technical_owner", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('present', 'deleted')", name=op.f("ck_source_systems_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_source_systems_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_systems_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_systems")),
        sa.UniqueConstraint("workspace_id", "code", name=op.f("uq_source_systems_workspace_id")),
    )


def downgrade() -> None:
    op.drop_table("source_systems")
