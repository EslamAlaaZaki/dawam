"""add data warehouses: the warehouse module's data_warehouses table.

Revision ID: d4a9e7b31c58
Revises: a83c5e1d9f42
Create Date: 2026-10-05 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a9e7b31c58"
down_revision: str | Sequence[str] | None = "a83c5e1d9f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_warehouses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("target_platform", sa.String(length=16), nullable=False),
        sa.Column("layer_physical_schemas", sa.JSON(), nullable=False),
        sa.Column("naming_rules", sa.JSON(), nullable=False),
        sa.Column("date_dim_settings", sa.JSON(), nullable=False),
        sa.Column("set_up_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_data_warehouses_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_warehouses")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_data_warehouses_workspace_id")),
    )


def downgrade() -> None:
    op.drop_table("data_warehouses")
