"""add llm providers and models: the llm module's tables.

Revision ID: a1f4c7e92b35
Revises: 03e7e6a0bac5
Create Date: 2026-10-06 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c7e92b35"
down_revision: str | Sequence[str] | None = "03e7e6a0bac5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_llm_providers_name")),
    )
    op.create_table(
        "llm_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("roles", sa.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=True),
        sa.Column("supports_streaming", sa.Boolean(), nullable=True),
        sa.Column("supports_json_schema", sa.Boolean(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("test_ok", sa.Boolean(), nullable=True),
        sa.Column("test_error_code", sa.String(length=32), nullable=True),
        sa.Column("test_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["llm_providers.id"],
            name=op.f("fk_llm_models_provider_id_llm_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_models")),
        sa.UniqueConstraint("provider_id", "name", name=op.f("uq_llm_models_provider_id")),
    )


def downgrade() -> None:
    op.drop_table("llm_models")
    op.drop_table("llm_providers")
