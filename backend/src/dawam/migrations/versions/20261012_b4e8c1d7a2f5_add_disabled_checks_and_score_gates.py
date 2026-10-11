"""add disabled score checks and per-Layer score gates (warehouse module).

Revision ID: b4e8c1d7a2f5
Revises: 9a3d5e7c1b28
Create Date: 2026-10-12 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8c1d7a2f5"
down_revision: str | Sequence[str] | None = "9a3d5e7c1b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disabled_checks",
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("check_code", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("disabled_by", sa.Uuid(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_disabled_checks_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by"],
            ["users.id"],
            name=op.f("fk_disabled_checks_disabled_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("data_warehouse_id", "check_code", name=op.f("pk_disabled_checks")),
    )
    op.create_table(
        "score_gates",
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False),
        sa.Column("block_on_errors", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("layer IN ('core', 'mart')", name=op.f("ck_score_gates_layer")),
        sa.CheckConstraint(
            "min_score >= 0 AND min_score <= 100", name=op.f("ck_score_gates_min_score")
        ),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_score_gates_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_score_gates_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("data_warehouse_id", "layer", name=op.f("pk_score_gates")),
    )


def downgrade() -> None:
    op.drop_table("score_gates")
    op.drop_table("disabled_checks")
