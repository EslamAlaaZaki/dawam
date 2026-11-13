"""PII findings are unique per column, rule and source, and ``ai`` is a source: the name
scan, the value scan and the AI PII detection each keep their own row for a rule (#601).
Existing rows stay as they are.

Revision ID: f3b8c1d6a924
Revises: a7c3e9d1f520
Create Date: 2026-11-13 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3b8c1d6a924"
down_revision: str | Sequence[str] | None = "a7c3e9d1f520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("uq_pii_findings_src_column_id"), "pii_findings", type_="unique")
    op.create_unique_constraint(
        op.f("uq_pii_findings_src_column_id"), "pii_findings", ["src_column_id", "rule", "source"]
    )
    op.drop_constraint(op.f("ck_pii_findings_source"), "pii_findings", type_="check")
    op.create_check_constraint(
        op.f("ck_pii_findings_source"), "pii_findings", "source IN ('name', 'value', 'ai')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM pii_findings WHERE source = 'ai'")
    # The name row of a rule wins; a value row of the same rule goes.
    op.execute(
        "DELETE FROM pii_findings v USING pii_findings n "
        "WHERE v.source = 'value' AND n.source = 'name' "
        "AND v.src_column_id = n.src_column_id AND v.rule = n.rule"
    )
    op.drop_constraint(op.f("ck_pii_findings_source"), "pii_findings", type_="check")
    op.create_check_constraint(
        op.f("ck_pii_findings_source"), "pii_findings", "source IN ('name', 'value')"
    )
    op.drop_constraint(op.f("uq_pii_findings_src_column_id"), "pii_findings", type_="unique")
    op.create_unique_constraint(
        op.f("uq_pii_findings_src_column_id"), "pii_findings", ["src_column_id", "rule"]
    )
