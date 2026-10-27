"""add the ordered model fallback list per role.

Revision ID: d4f6a8c2e139
Revises: d6a3f8b1c472
Create Date: 2026-10-26 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8c2e139"
down_revision: str | Sequence[str] | None = "d6a3f8b1c472"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_role_fallbacks",
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("role IN ('agent', 'light')", name=op.f("ck_llm_role_fallbacks_role")),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name=op.f("fk_llm_role_fallbacks_model_id_llm_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role", "model_id", name=op.f("pk_llm_role_fallbacks")),
        sa.UniqueConstraint("role", "position", name=op.f("uq_llm_role_fallbacks_role_position")),
    )


def downgrade() -> None:
    op.drop_table("llm_role_fallbacks")
