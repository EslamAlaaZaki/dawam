"""add invitations: the auth module's invitations table.

Revision ID: a83c5e1d9f42
Revises: 4f1d8c2b9a6e
Create Date: 2026-10-04 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a83c5e1d9f42"
down_revision: str | Sequence[str] | None = "4f1d8c2b9a6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_role", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "workspace_role IN ('owner', 'editor', 'viewer')",
            name=op.f("ck_invitations_workspace_role"),
        ),
        sa.CheckConstraint(
            "(workspace_id IS NULL) = (workspace_role IS NULL)",
            name=op.f("ck_invitations_workspace_and_role"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name=op.f("fk_invitations_invited_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_invitations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(op.f("ix_invitations_email"), "invitations", ["email"], unique=False)
    op.create_index(
        op.f("ix_invitations_workspace_id"), "invitations", ["workspace_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_invitations_workspace_id"), table_name="invitations")
    op.drop_index(op.f("ix_invitations_email"), table_name="invitations")
    op.drop_table("invitations")
