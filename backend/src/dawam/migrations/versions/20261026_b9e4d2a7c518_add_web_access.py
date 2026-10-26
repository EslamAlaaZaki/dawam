"""Add web access for the assistant: the switch and the search providers (ADR 0008).

Revision ID: b9e4d2a7c518
Revises: f1a7c3d9b524
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4d2a7c518"
down_revision: str | Sequence[str] | None = "f1a7c3d9b524"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("normal_provider", sa.String(length=16), nullable=True),
        sa.Column("internal_provider", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_settings")),
    )
    op.create_table(
        "web_search_providers",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("test_ok", sa.Boolean(), nullable=True),
        sa.Column("test_error", sa.Text(), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("kind", name=op.f("pk_web_search_providers")),
    )


def downgrade() -> None:
    op.drop_table("web_search_providers")
    op.drop_table("web_settings")
