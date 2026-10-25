"""Add the ``rerank`` model role (a Workspace's optional rerank model, which reorders the top
document-search results).

Revision ID: b5e9c3a7d216
Revises: e5a1c8d7b362
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b5e9c3a7d216"
down_revision: str | Sequence[str] | None = "e5a1c8d7b362"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "llm_workspace_models"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_llm_workspace_models_role"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_llm_workspace_models_single_is_default"), TABLE, type_="check")
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_role"),
        TABLE,
        "role IN ('agent', 'light', 'embedding', 'vision', 'rerank')",
    )
    op.create_check_constraint(
        op.f("ck_llm_workspace_models_single_is_default"),
        TABLE,
        "role NOT IN ('embedding', 'vision', 'rerank') OR is_default",
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE role = 'rerank'")
    op.execute("UPDATE llm_models SET roles = array_remove(roles, 'rerank')")
    op.drop_constraint(op.f("ck_llm_workspace_models_single_is_default"), TABLE, type_="check")
    op.drop_constraint(op.f("ck_llm_workspace_models_role"), TABLE, type_="check")
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
