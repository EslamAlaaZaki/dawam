"""add relationships.

Revision ID: 7f3a91c0d2b8
Revises: 8f3a1c7e52b9
Create Date: 2026-10-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f3a91c0d2b8"
down_revision: str | Sequence[str] | None = "8f3a1c7e52b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_column_id", sa.Uuid(), nullable=False),
        sa.Column("to_column_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "origin IN ('declared', 'inferred', 'routine', 'ai', 'manual')",
            name=op.f("ck_relationships_origin"),
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'rejected')", name=op.f("ck_relationships_status")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_relationships_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["from_column_id"],
            ["src_columns.id"],
            name=op.f("fk_relationships_from_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_column_id"],
            ["src_columns.id"],
            name=op.f("fk_relationships_to_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
        sa.UniqueConstraint(
            "from_column_id", "to_column_id", name=op.f("uq_relationships_from_column_id")
        ),
    )
    op.create_index(
        op.f("ix_relationships_from_column_id"), "relationships", ["from_column_id"], unique=False
    )
    op.create_index(
        op.f("ix_relationships_to_column_id"), "relationships", ["to_column_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_relationships_to_column_id"), table_name="relationships")
    op.drop_index(op.f("ix_relationships_from_column_id"), table_name="relationships")
    op.drop_table("relationships")
