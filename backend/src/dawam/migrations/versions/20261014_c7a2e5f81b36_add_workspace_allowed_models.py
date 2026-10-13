"""add per-Workspace Allowed Models.

Revision ID: c7a2e5f81b36
Revises: 5d1a8e3c7f42
Create Date: 2026-10-14 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a2e5f81b36"
down_revision: str | Sequence[str] | None = "5d1a8e3c7f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No rows for a Workspace means every registered model is allowed.
    op.create_table(
        "llm_workspace_allowed_models",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_llm_workspace_allowed_models_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name=op.f("fk_llm_workspace_allowed_models_model_id_llm_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "model_id", name=op.f("pk_llm_workspace_allowed_models")
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_workspace_allowed_models")
