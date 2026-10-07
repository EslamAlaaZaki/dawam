"""add per-Workspace AI settings (model, internal-only, data-sharing level).

Revision ID: 9a4e1c7b3f58
Revises: 7f3a91c0d2b8
Create Date: 2026-10-07 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a4e1c7b3f58"
down_revision: str | Sequence[str] | None = "7f3a91c0d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_workspace_settings",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("internal_only", sa.Boolean(), nullable=False),
        sa.Column("data_sharing_level", sa.String(length=16), nullable=False),
        sa.Column("agent_model_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "data_sharing_level IN ('metadata', 'profiles', 'documents', 'samples')",
            name=op.f("ck_llm_workspace_settings_data_sharing_level"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_llm_workspace_settings_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_model_id"],
            ["llm_models.id"],
            name=op.f("fk_llm_workspace_settings_agent_model_id_llm_models"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_llm_workspace_settings")),
    )
    # Workspaces that exist already keep behaving as they did: nothing is restricted.
    op.execute(
        "INSERT INTO llm_workspace_settings "
        "(workspace_id, internal_only, data_sharing_level, updated_at) "
        "SELECT id, false, 'metadata', now() FROM workspaces"
    )


def downgrade() -> None:
    op.drop_table("llm_workspace_settings")
