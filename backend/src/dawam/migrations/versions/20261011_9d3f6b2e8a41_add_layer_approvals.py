"""add layer approvals: the Core and Mart Layer Approval (warehouse module) and the owner
setting "approver must not be the last editor" (workspaces module).

Revision ID: 9d3f6b2e8a41
Revises: 7c2e9f4a1b36
Create Date: 2026-10-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d3f6b2e8a41"
down_revision: str | Sequence[str] | None = "7c2e9f4a1b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column(
            "require_distinct_approver", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_table(
        "layer_approvals",
        sa.Column("data_warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_editor_id", sa.Uuid(), nullable=True),
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("layer IN ('core', 'mart')", name=op.f("ck_layer_approvals_layer")),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved')", name=op.f("ck_layer_approvals_status")
        ),
        sa.ForeignKeyConstraint(
            ["data_warehouse_id"],
            ["data_warehouses.id"],
            name=op.f("fk_layer_approvals_data_warehouse_id_data_warehouses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name=op.f("fk_layer_approvals_approved_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_editor_id"],
            ["users.id"],
            name=op.f("fk_layer_approvals_last_editor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("data_warehouse_id", "layer", name=op.f("pk_layer_approvals")),
    )


def downgrade() -> None:
    op.drop_table("layer_approvals")
    op.drop_column("workspaces", "require_distinct_approver")
