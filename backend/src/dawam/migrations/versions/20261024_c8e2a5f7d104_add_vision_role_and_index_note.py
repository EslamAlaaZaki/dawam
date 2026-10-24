"""Add the ``vision`` model role (a Workspace's optional vision model, for OCR) and the per-file
index note (e.g. that the self-hosted OCR engine stood in for the vision model).

Revision ID: c8e2a5f7d104
Revises: f1b6c3a8d902
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a5f7d104"
down_revision: str | Sequence[str] | None = "f1b6c3a8d902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "llm_workspace_models"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_llm_workspace_models_role"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_llm_workspace_models_embedding_is_default"), TABLE, type_="check")
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_role"),
        TABLE,
        "role IN ('agent', 'light', 'embedding', 'vision')",
    )
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_single_is_default"),
        TABLE,
        "role NOT IN ('embedding', 'vision') OR is_default",
    )
    op.add_column("workspace_files", sa.Column("index_note", sa.Text()))


def downgrade() -> None:
    op.drop_column("workspace_files", "index_note")
    op.execute(f"DELETE FROM {TABLE} WHERE role = 'vision'")
    op.execute("UPDATE llm_models SET roles = array_remove(roles, 'vision')")
    op.drop_constraint(op.f("ck_llm_workspace_models_single_is_default"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_llm_workspace_models_role"), TABLE, type_="check")
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_role"), TABLE, "role IN ('agent', 'light', 'embedding')"
    )
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_embedding_is_default"),
        TABLE,
        "role <> 'embedding' OR is_default",
    )
