"""add the Workspace's embedding model, fixed at creation.

Revision ID: f4c9a2b7d815
Revises: e3b8d1a6f204
Create Date: 2026-10-14 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4c9a2b7d815"
down_revision: str | Sequence[str] | None = "e3b8d1a6f204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_workspace_settings", sa.Column("embedding_model_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_llm_workspace_settings_embedding_model_id_llm_models"),
        "llm_workspace_settings",
        "llm_models",
        ["embedding_model_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Existing Workspaces keep using the installation's current embedding model.
    op.execute(
        "UPDATE llm_workspace_settings SET embedding_model_id = "
        "(SELECT embedding_model_id FROM llm_settings WHERE id = 1)"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_llm_workspace_settings_embedding_model_id_llm_models"),
        "llm_workspace_settings",
        type_="foreignkey",
    )
    op.drop_column("llm_workspace_settings", "embedding_model_id")
