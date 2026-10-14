"""Workspace Models replace the installation's role defaults and Allowed Models.

Each Workspace's owner chooses its embedding, agent and light models (``llm_workspace_models``);
the installation keeps only its budget. The product is not live, so no data is carried
across: the database is reset.

Revision ID: b6f2d9e4a713
Revises: a9d4f7c2e1b8
Create Date: 2026-10-14 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6f2d9e4a713"
down_revision: str | Sequence[str] | None = "a9d4f7c2e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS_COLUMNS = (
    "agent_model_id",
    "light_model_id",
    "embedding_model_id",
    "embedding_dimension",
    "reindex_needed",
    "reindex_reason",
    "reindex_flagged_at",
)
WORKSPACE_COLUMNS = ("agent_model_id", "light_model_id", "embedding_model_id")


def upgrade() -> None:
    op.drop_table("llm_workspace_allowed_models")
    for column in WORKSPACE_COLUMNS:
        op.drop_column("llm_workspace_settings", column)
    for column in SETTINGS_COLUMNS:
        op.drop_column("llm_settings", column)
    op.create_table(
        "llm_workspace_models",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "role IN ('agent', 'light', 'embedding')", name=op.f("ck_llm_workspace_models_role")
        ),
        sa.CheckConstraint(
            "role <> 'embedding' OR is_default",
            name=op.f("ck_llm_workspace_models_embedding_is_default"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_llm_workspace_models_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name=op.f("fk_llm_workspace_models_model_id_llm_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "model_id", "role", name=op.f("pk_llm_workspace_models")
        ),
    )
    op.create_index(
        "uq_llm_workspace_models_default",
        "llm_workspace_models",
        ["workspace_id", "role"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_workspace_models_default", table_name="llm_workspace_models")
    op.drop_table("llm_workspace_models")
    op.add_column("llm_settings", sa.Column("agent_model_id", sa.Uuid(), nullable=True))
    op.add_column("llm_settings", sa.Column("light_model_id", sa.Uuid(), nullable=True))
    op.add_column("llm_settings", sa.Column("embedding_model_id", sa.Uuid(), nullable=True))
    op.add_column("llm_settings", sa.Column("embedding_dimension", sa.Integer(), nullable=True))
    op.add_column(
        "llm_settings",
        sa.Column("reindex_needed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("llm_settings", "reindex_needed", server_default=None)
    op.add_column("llm_settings", sa.Column("reindex_reason", sa.String(length=64), nullable=True))
    op.add_column(
        "llm_settings",
        sa.Column("reindex_flagged_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in SETTINGS_COLUMNS[:3]:
        op.create_foreign_key(
            op.f(f"fk_llm_settings_{column}_llm_models"),
            "llm_settings",
            "llm_models",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
    for column in WORKSPACE_COLUMNS:
        op.add_column("llm_workspace_settings", sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_llm_workspace_settings_{column}_llm_models"),
            "llm_workspace_settings",
            "llm_models",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
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
