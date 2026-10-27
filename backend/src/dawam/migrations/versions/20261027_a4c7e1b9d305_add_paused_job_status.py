"""Let a job wait for the member's answer: a ``paused`` status (#402).

Revision ID: a4c7e1b9d305
Revises: a4c7e1b8d935
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4c7e1b9d305"
down_revision: str | Sequence[str] | None = "a4c7e1b8d935"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"
NEW = "status IN ('queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_status"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status"), "jobs", NEW)


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'cancelled' WHERE status = 'paused'")
    op.drop_constraint(op.f("ck_jobs_status"), "jobs", type_="check")
    op.create_check_constraint(op.f("ck_jobs_status"), "jobs", OLD)
