"""add admin and sign-up limit: the admin module's system_settings table, and the auth
module's registration_attempts (the per-address sign-up limit).

Revision ID: bba343761c52
Revises: 7c3e9a1f5b20
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bba343761c52"
down_revision: str | Sequence[str] | None = "7c3e9a1f5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )
    op.create_table(
        "registration_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_attempts")),
    )
    op.create_index(
        "ix_registration_attempts_ip_attempted_at",
        "registration_attempts",
        ["ip", "attempted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_registration_attempts_attempted_at"),
        "registration_attempts",
        ["attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_registration_attempts_attempted_at"), table_name="registration_attempts")
    op.drop_index("ix_registration_attempts_ip_attempted_at", table_name="registration_attempts")
    op.drop_table("registration_attempts")
    op.drop_table("system_settings")
