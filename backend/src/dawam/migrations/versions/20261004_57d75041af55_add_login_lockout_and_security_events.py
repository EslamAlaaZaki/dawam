"""add login lockout and security events: the auth module's security_events table.

Revision ID: 57d75041af55
Revises: e54c99dddac2
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "57d75041af55"
down_revision: str | Sequence[str] | None = "e54c99dddac2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
        sa.UniqueConstraint("seq", name=op.f("uq_security_events_seq")),
    )
    op.create_index(
        op.f("ix_security_events_actor_id"), "security_events", ["actor_id"], unique=False
    )
    op.create_index(
        op.f("ix_security_events_created_at"), "security_events", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_security_events_target_id"), "security_events", ["target_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_security_events_target_id"), table_name="security_events")
    op.drop_index(op.f("ix_security_events_created_at"), table_name="security_events")
    op.drop_index(op.f("ix_security_events_actor_id"), table_name="security_events")
    op.drop_table("security_events")
