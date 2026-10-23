"""Add the model price table (per million tokens) used to cost AI usage.

Revision ID: b3d7f1a9c502
Revises: d4e7a1c9b5f2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7f1a9c502"
down_revision: str | Sequence[str] | None = "d4e7a1c9b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_model_prices",
        sa.Column("model_name", sa.String(200), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("input_per_million", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_per_million", sa.Numeric(18, 6), nullable=False),
        sa.Column("cached_input_per_million", sa.Numeric(18, 6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0 AND cached_input_per_million >= 0",
            name="non_negative",
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_model_prices")
