"""add lookup and system mapping types and a column mapping's lookup spec.

Revision ID: 5f2b9d8e4a17
Revises: 3c8e5f1a7d92
Create Date: 2026-10-07 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5f2b9d8e4a17"
down_revision: str | Sequence[str] | None = "3c8e5f1a7d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = "'direct', 'derived', 'constant', 'not_in_branch', 'unmapped'"
NEW_TYPES = "'direct', 'derived', 'constant', 'lookup', 'system', 'not_in_branch', 'unmapped'"


def upgrade() -> None:
    op.add_column("column_mappings", sa.Column("lookup", sa.JSON(), nullable=True))
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({NEW_TYPES})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM column_mappings WHERE mapping_type IN ('lookup', 'system')")
    op.drop_constraint(op.f("ck_column_mappings_mapping_type"), "column_mappings", type_="check")
    op.create_check_constraint(
        op.f("ck_column_mappings_mapping_type"),
        "column_mappings",
        f"mapping_type IN ({OLD_TYPES})",
    )
    op.drop_column("column_mappings", "lookup")
