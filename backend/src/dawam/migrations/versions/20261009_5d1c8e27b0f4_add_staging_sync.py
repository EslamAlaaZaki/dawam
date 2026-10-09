"""add staging sync.

Revision ID: 5d1c8e27b0f4
Revises: a3f90c1d7b52
Create Date: 2026-10-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d1c8e27b0f4"
down_revision: str | Sequence[str] | None = "a3f90c1d7b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "status IN ('present', 'source_removed', 'deleted')"


def upgrade() -> None:
    for table in ("dw_tables", "dw_columns"):
        op.add_column(
            table,
            sa.Column("status", sa.String(16), server_default="present", nullable=False),
        )
        op.add_column(
            table,
            sa.Column("edited_fields", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        )
        op.create_check_constraint(op.f(f"ck_{table}_status"), table, _STATUS_CHECK)
    op.create_table(
        "tombstones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("object_type", sa.String(16), nullable=False),
        sa.Column("generation_key", sa.String(128), nullable=True),
        sa.Column("src_object_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["data_warehouse_id"], ["data_warehouses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tombstones_data_warehouse_id"), "tombstones", ["data_warehouse_id"])
    op.create_index(
        "uq_tombstones_staging_source",
        "tombstones",
        ["data_warehouse_id", "src_object_id"],
        unique=True,
        postgresql_where=sa.text("object_type = 'staging_table'"),
    )


def downgrade() -> None:
    op.drop_index("uq_tombstones_staging_source", table_name="tombstones")
    op.drop_index(op.f("ix_tombstones_data_warehouse_id"), table_name="tombstones")
    op.drop_table("tombstones")
    for table in ("dw_columns", "dw_tables"):
        op.drop_constraint(op.f(f"ck_{table}_status"), table, type_="check")
        op.drop_column(table, "edited_fields")
        op.drop_column(table, "status")
