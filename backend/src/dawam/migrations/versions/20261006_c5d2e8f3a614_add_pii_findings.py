"""add PII findings and the confirmed PII category on Source Columns.

Revision ID: c5d2e8f3a614
Revises: b2c8d4f61a97
Create Date: 2026-10-06 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d2e8f3a614"
down_revision: str | Sequence[str] | None = "b2c8d4f61a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("src_columns", sa.Column("pii_category", sa.String(length=32), nullable=True))
    op.create_table(
        "pii_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("src_column_id", sa.Uuid(), nullable=False),
        sa.Column("rule", sa.String(length=48), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('direct_identifier', 'quasi_identifier', 'sensitive', 'financial')",
            name=op.f("ck_pii_findings_category"),
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'dismissed')",
            name=op.f("ck_pii_findings_status"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_pii_findings_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_pii_findings_snapshot_id_snapshots"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["src_column_id"],
            ["src_columns.id"],
            name=op.f("fk_pii_findings_src_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pii_findings")),
        sa.UniqueConstraint("src_column_id", "rule", name=op.f("uq_pii_findings_src_column_id")),
    )
    op.create_index(
        op.f("ix_pii_findings_src_column_id"), "pii_findings", ["src_column_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pii_findings_src_column_id"), table_name="pii_findings")
    op.drop_table("pii_findings")
    op.drop_column("src_columns", "pii_category")
