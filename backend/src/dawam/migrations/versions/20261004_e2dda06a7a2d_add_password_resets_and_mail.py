"""add password resets and mail: the auth module's password_resets table, and the mail
module's smtp_settings and undelivered_links tables.

Revision ID: e2dda06a7a2d
Revises: 7c3e9a1f5b20
Create Date: 2026-10-04 09:09:14.126288
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2dda06a7a2d"
down_revision: str | Sequence[str] | None = "7c3e9a1f5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "undelivered_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("url_encrypted", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_undelivered_links")),
    )
    op.create_index(
        op.f("ix_undelivered_links_expires_at"), "undelivered_links", ["expires_at"], unique=False
    )
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_resets_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_resets_token_hash")),
    )
    op.create_index(
        op.f("ix_password_resets_user_id"), "password_resets", ["user_id"], unique=False
    )
    op.create_index(
        "ix_password_resets_requested_ip_created_at",
        "password_resets",
        ["requested_ip", "created_at"],
        unique=False,
    )
    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("security", sa.String(length=16), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "security IN ('none', 'starttls', 'tls')", name=op.f("ck_smtp_settings_security")
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_smtp_settings_single_row")),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name=op.f("ck_smtp_settings_port")),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_smtp_settings_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_smtp_settings")),
    )


def downgrade() -> None:
    op.drop_table("smtp_settings")
    op.drop_index("ix_password_resets_requested_ip_created_at", table_name="password_resets")
    op.drop_index(op.f("ix_password_resets_user_id"), table_name="password_resets")
    op.drop_table("password_resets")
    op.drop_index(op.f("ix_undelivered_links_expires_at"), table_name="undelivered_links")
    op.drop_table("undelivered_links")
