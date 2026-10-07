"""add change sets and their items.

Revision ID: 712926c5eed8
Revises: 3c8e5f1a7d92
Create Date: 2026-10-07 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "712926c5eed8"
down_revision: str | Sequence[str] | None = "3c8e5f1a7d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINS = (
    "'ai', 'regeneration', 'sync', 'propagation', 'import', 'platform_change', 'system_code_change'"
)
SET_STATUSES = "'pending', 'applied', 'partially_applied', 'rejected', 'superseded'"
ITEM_STATUSES = "'pending', 'needs_owner', 'accepted', 'rejected', 'stale', 'expired'"


def upgrade() -> None:
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_by", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"origin IN ({ORIGINS})", name=op.f("ck_change_sets_origin")),
        sa.CheckConstraint(f"status IN ({SET_STATUSES})", name=op.f("ck_change_sets_status")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_change_sets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_change_sets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["applied_by"],
            ["users.id"],
            name=op.f("fk_change_sets_applied_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_sets")),
    )
    op.create_index(
        "ix_change_sets_workspace", "change_sets", ["workspace_id", "created_at"], unique=False
    )
    op.create_table(
        "change_set_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("base_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("depends_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_role", sa.String(length=8), nullable=False),
        sa.Column("is_conflict", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.String(length=300), nullable=True),
        sa.CheckConstraint(
            "operation IN ('create', 'update', 'delete')",
            name=op.f("ck_change_set_items_operation"),
        ),
        sa.CheckConstraint(
            "required_role IN ('editor', 'owner')", name=op.f("ck_change_set_items_required_role")
        ),
        sa.CheckConstraint(f"status IN ({ITEM_STATUSES})", name=op.f("ck_change_set_items_status")),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_change_set_items_change_set_id_change_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_set_items")),
    )
    op.create_index(
        "ix_change_set_items_change_set",
        "change_set_items",
        ["change_set_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_change_set_items_change_set", table_name="change_set_items")
    op.drop_table("change_set_items")
    op.drop_index("ix_change_sets_workspace", table_name="change_sets")
    op.drop_table("change_sets")
