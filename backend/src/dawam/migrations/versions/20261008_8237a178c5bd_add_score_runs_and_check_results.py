"""add score runs and check results.

Revision ID: 8237a178c5bd
Revises: b94e07d3a1c8
Create Date: 2026-10-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8237a178c5bd"
down_revision: str | Sequence[str] | None = "b94e07d3a1c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "score_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("grade", sa.String(length=1), nullable=True),
        sa.Column("per_layer", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_score_runs_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_runs")),
    )
    op.create_index(
        "ix_score_runs_dw_created_at", "score_runs", ["data_warehouse_id", "created_at"]
    )
    op.create_table(
        "score_check_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("check_code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("object_name", sa.String(length=300), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_score_check_results_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_check_results")),
    )
    op.create_index(
        op.f("ix_score_check_results_data_warehouse_id"),
        "score_check_results",
        ["data_warehouse_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_score_check_results_data_warehouse_id"), table_name="score_check_results"
    )
    op.drop_table("score_check_results")
    op.drop_index("ix_score_runs_dw_created_at", table_name="score_runs")
    op.drop_table("score_runs")
