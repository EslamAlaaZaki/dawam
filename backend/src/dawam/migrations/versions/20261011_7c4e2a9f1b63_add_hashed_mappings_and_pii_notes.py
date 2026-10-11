"""add the hashed mapping type and a DW column's PII handling note.

Revision ID: 7c4e2a9f1b63
Revises: e3a7c91b5d20
Create Date: 2026-10-11 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e2a9f1b63"
down_revision: str | Sequence[str] | None = "e3a7c91b5d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "'direct', 'derived', 'constant', 'lookup', 'system', 'not_in_branch', 'unmapped'"
NEW_TYPES = (
    "'direct', 'hashed', 'derived', 'constant', 'lookup', 'system', 'not_in_branch', 'unmapped'"
)


def upgrade() -> None:
    op.add_column("dw_columns", sa.Column("pii_note", sa.String(1000), nullable=True))
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({NEW_TYPES})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM column_mappings WHERE mapping_type = 'hashed'")
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({OLD_TYPES})",
    )
    op.drop_column("dw_columns", "pii_note")
