"""KPI review like PII: a review state apart from the status (live, suggested, accepted,
rejected), who decided and when, and whether a removal rejected it. Existing KPIs become
live, or accepted for the AI's (#595).

Revision ID: b3e8d1f4a926
Revises: a7c3e9d1f520
Create Date: 2026-11-13 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e8d1f4a926"
down_revision: str | Sequence[str] | None = "a7c3e9d1f520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kpis",
        sa.Column("review_state", sa.String(length=16), server_default="live", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_kpis_review_state"),
        "kpis",
        "review_state IN ('live', 'suggested', 'accepted', 'rejected')",
    )
    op.add_column("kpis", sa.Column("decided_by", sa.Uuid(), nullable=True))
    op.add_column("kpis", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "kpis", sa.Column("removed", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.create_foreign_key(
        op.f("fk_kpis_decided_by_users"),
        "kpis",
        "users",
        ["decided_by"],
        ["id"],
        ondelete="SET NULL",
    )
    # The AI's KPIs were already in the catalog: accepted when they were created.
    op.execute(
        "UPDATE kpis SET review_state = 'accepted', decided_by = created_by, "
        "decided_at = created_at WHERE origin = 'ai'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_kpis_decided_by_users"), "kpis", type_="foreignkey")
    op.drop_column("kpis", "removed")
    op.drop_column("kpis", "decided_at")
    op.drop_column("kpis", "decided_by")
    op.drop_constraint(op.f("ck_kpis_review_state"), "kpis", type_="check")
    op.drop_column("kpis", "review_state")
