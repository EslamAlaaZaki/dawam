"""AI runs (#631): each ``llm_usage`` row records what the call was made for (``feature``,
and ``job_id`` for a background job), how long it took (``duration_ms``) and whether it
failed (``status``, ``error_code``). Rows recorded before are ``unknown`` and ``ok``.

Revision ID: e8b3f1a6c254
Revises: d4e7a2c9f135
Create Date: 2026-11-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3f1a6c254"
down_revision: str | Sequence[str] | None = "d4e7a2c9f135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_usage",
        sa.Column("feature", sa.String(length=32), server_default="unknown", nullable=False),
    )
    op.add_column("llm_usage", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.add_column("llm_usage", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column(
        "llm_usage",
        sa.Column("status", sa.String(length=8), server_default="ok", nullable=False),
    )
    op.add_column("llm_usage", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.create_foreign_key(
        op.f("fk_llm_usage_job_id_jobs"),
        "llm_usage",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        op.f("ck_llm_usage_status"), "llm_usage", "status IN ('ok', 'failed')"
    )
    op.create_index(op.f("ix_llm_usage_feature_created_at"), "llm_usage", ["feature", "created_at"])
    op.create_index(op.f("ix_llm_usage_job_id_created_at"), "llm_usage", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_usage_job_id_created_at"), table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_feature_created_at"), table_name="llm_usage")
    op.drop_constraint(op.f("ck_llm_usage_status"), "llm_usage", type_="check")
    op.drop_constraint(op.f("fk_llm_usage_job_id_jobs"), "llm_usage", type_="foreignkey")
    for column in ("error_code", "status", "duration_ms", "job_id", "feature"):
        op.drop_column("llm_usage", column)
