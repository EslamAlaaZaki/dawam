"""PII review by column: findings are accepted or rejected (no longer confirmed or
dismissed), remember the confidence at rejection, whether a removal rejected them and what
scan they come from; Source Columns record who added them as PII by hand, and their PII
category is one of the four (#589).

Revision ID: a7c3e9d1f520
Revises: e4b7a1c9d352
Create Date: 2026-11-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9d1f520"
down_revision: str | Sequence[str] | None = "e4b7a1c9d352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = "('direct_identifier', 'quasi_identifier', 'sensitive', 'financial')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_pii_findings_status"), "pii_findings", type_="check")
    op.execute("UPDATE pii_findings SET status = 'accepted' WHERE status = 'confirmed'")
    op.execute("UPDATE pii_findings SET status = 'rejected' WHERE status = 'dismissed'")
    op.create_check_constraint(
        op.f("ck_pii_findings_status"),
        "pii_findings",
        "status IN ('suggested', 'accepted', 'rejected')",
    )
    op.add_column(
        "pii_findings",
        sa.Column("source", sa.String(length=8), server_default="name", nullable=False),
    )
    # Value scans record no Snapshot; a query's values are a value finding too.
    op.execute(
        "UPDATE pii_findings SET source = 'value' "
        "WHERE snapshot_id IS NULL OR rule = 'value-at-query'"
    )
    op.create_check_constraint(
        op.f("ck_pii_findings_source"), "pii_findings", "source IN ('name', 'value')"
    )
    op.add_column("pii_findings", sa.Column("confidence_at_rejection", sa.Float(), nullable=True))
    op.execute(
        "UPDATE pii_findings SET confidence_at_rejection = confidence WHERE status = 'rejected'"
    )
    op.add_column(
        "pii_findings",
        sa.Column("removed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.add_column("src_columns", sa.Column("pii_added_by", sa.Uuid(), nullable=True))
    op.add_column(
        "src_columns", sa.Column("pii_added_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_src_columns_pii_added_by_users"),
        "src_columns",
        "users",
        ["pii_added_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(f"UPDATE src_columns SET pii_category = NULL WHERE pii_category NOT IN {CATEGORIES}")
    op.create_check_constraint(
        op.f("ck_src_columns_pii_category"),
        "src_columns",
        f"pii_category IS NULL OR pii_category IN {CATEGORIES}",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_src_columns_pii_category"), "src_columns", type_="check")
    op.drop_constraint(op.f("fk_src_columns_pii_added_by_users"), "src_columns", type_="foreignkey")
    op.drop_column("src_columns", "pii_added_at")
    op.drop_column("src_columns", "pii_added_by")
    op.drop_column("pii_findings", "removed")
    op.drop_column("pii_findings", "confidence_at_rejection")
    op.drop_constraint(op.f("ck_pii_findings_source"), "pii_findings", type_="check")
    op.drop_column("pii_findings", "source")
    op.drop_constraint(op.f("ck_pii_findings_status"), "pii_findings", type_="check")
    op.execute("UPDATE pii_findings SET status = 'confirmed' WHERE status = 'accepted'")
    op.execute("UPDATE pii_findings SET status = 'dismissed' WHERE status = 'rejected'")
    op.create_check_constraint(
        op.f("ck_pii_findings_status"),
        "pii_findings",
        "status IN ('suggested', 'confirmed', 'dismissed')",
    )
