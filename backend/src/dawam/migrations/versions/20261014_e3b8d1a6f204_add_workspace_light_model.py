"""add the Workspace's own light model.

Revision ID: e3b8d1a6f204
Revises: c7a2e5f81b36
Create Date: 2026-10-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b8d1a6f204"
down_revision: str | Sequence[str] | None = "c7a2e5f81b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("llm_workspace_settings", sa.Column("light_model_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_llm_workspace_settings_light_model_id_llm_models"),
        "llm_workspace_settings",
        "llm_models",
        ["light_model_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_llm_workspace_settings_light_model_id_llm_models"),
        "llm_workspace_settings",
        type_="foreignkey",
    )
    op.drop_column("llm_workspace_settings", "light_model_id")
