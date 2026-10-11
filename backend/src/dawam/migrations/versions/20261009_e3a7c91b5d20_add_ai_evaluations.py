"""add ai evaluations: the stored advisory evaluations of a Layer (warehouse module).

Revision ID: e3a7c91b5d20
Revises: 5d1c8e27b0f4
Create Date: 2026-10-09 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a7c91b5d20"
down_revision: str | Sequence[str] | None = "5d1c8e27b0f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["data_warehouse_id"], ["data_warehouses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_evaluations_dw_layer_created_at",
        "ai_evaluations",
        ["data_warehouse_id", "layer", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_evaluations_dw_layer_created_at", table_name="ai_evaluations")
    op.drop_table("ai_evaluations")
