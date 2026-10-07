"""add custom PII rules and per-Workspace disabled built-in rules.

Revision ID: 8f3a1c7e52b9
Revises: e7a1c4d9f2b8
Create Date: 2026-10-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3a1c7e52b9"
down_revision: str | Sequence[str] | None = "e7a1c4d9f2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pii_custom_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("keywords", sa.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("pattern", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('direct_identifier', 'quasi_identifier', 'sensitive', 'financial')",
            name=op.f("ck_pii_custom_rules_category"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_pii_custom_rules_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_pii_custom_rules_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pii_custom_rules")),
        sa.UniqueConstraint("workspace_id", "name", name=op.f("uq_pii_custom_rules_workspace_id")),
    )
    op.create_index(
        op.f("ix_pii_custom_rules_workspace_id"), "pii_custom_rules", ["workspace_id"], unique=False
    )
    op.create_table(
        "pii_disabled_rules",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("rule", sa.String(length=48), nullable=False),
        sa.Column("disabled_by", sa.Uuid(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["disabled_by"],
            ["users.id"],
            name=op.f("fk_pii_disabled_rules_disabled_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_pii_disabled_rules_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "rule", name=op.f("pk_pii_disabled_rules")),
    )


def downgrade() -> None:
    op.drop_table("pii_disabled_rules")
    op.drop_index(op.f("ix_pii_custom_rules_workspace_id"), table_name="pii_custom_rules")
    op.drop_table("pii_custom_rules")
