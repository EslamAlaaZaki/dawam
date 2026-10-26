"""Add the Document AI settings: installation defaults and bounds, Workspace overrides.

The Workspace's chunking strategy moves from its AI settings into its overrides: only a
strategy other than the default ``headings`` becomes an override.

Revision ID: c8f2a4d6e913
Revises: e4b8a1c6d937
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c8f2a4d6e913"
down_revision: str | Sequence[str] | None = "e4b8a1c6d937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_document_ai_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("knob_values", JSONB(), nullable=False),
        sa.Column("knob_bounds", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "llm_workspace_document_ai",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("overrides", JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO llm_workspace_document_ai (workspace_id, overrides, version, updated_at) "
        "SELECT workspace_id, jsonb_build_object('chunking_strategy', chunking_strategy), 1, "
        "updated_at FROM llm_workspace_settings WHERE chunking_strategy <> 'headings'"
    )
    op.drop_constraint(
        op.f("ck_llm_workspace_settings_chunking_strategy"),
        "llm_workspace_settings",
        type_="check",
    )
    op.drop_column("llm_workspace_settings", "chunking_strategy")


def downgrade() -> None:
    op.add_column(
        "llm_workspace_settings",
        sa.Column("chunking_strategy", sa.String(16), nullable=False, server_default="headings"),
    )
    op.create_check_constraint(
        op.f("ck_llm_workspace_settings_chunking_strategy"),
        "llm_workspace_settings",
        "chunking_strategy IN ('headings', 'recursive', 'semantic')",
    )
    op.execute(
        "UPDATE llm_workspace_settings s SET chunking_strategy = o.overrides->>'chunking_strategy' "
        "FROM llm_workspace_document_ai o WHERE o.workspace_id = s.workspace_id "
        "AND o.overrides ? 'chunking_strategy'"
    )
    op.drop_table("llm_workspace_document_ai")
    op.drop_table("llm_document_ai_settings")
