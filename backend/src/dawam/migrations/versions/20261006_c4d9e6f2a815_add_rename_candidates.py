"""add rename candidates: proposed renames of removed Source Objects.

Revision ID: c4d9e6f2a815
Revises: b2c8d4f61a97
Create Date: 2026-10-06 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e6f2a815"
down_revision: str | Sequence[str] | None = "b2c8d4f61a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rename_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("old_object_id", sa.Uuid(), nullable=False),
        sa.Column("new_object_id", sa.Uuid(), nullable=False),
        sa.Column("new_name", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('db_schema', 'table', 'column')",
            name=op.f("ck_rename_candidates_object_type"),
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'rejected')",
            name=op.f("ck_rename_candidates_status"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_rename_candidates_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rename_candidates")),
        sa.UniqueConstraint(
            "snapshot_id",
            "object_type",
            "old_object_id",
            "new_object_id",
            name=op.f("uq_rename_candidates_snapshot_id"),
        ),
    )
    op.create_index(op.f("ix_rename_candidates_snapshot_id"), "rename_candidates", ["snapshot_id"])


def downgrade() -> None:
    op.drop_table("rename_candidates")
