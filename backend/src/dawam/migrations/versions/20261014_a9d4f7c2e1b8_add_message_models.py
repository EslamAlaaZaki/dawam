"""remember the model of each assistant message (assistant module).

Revision ID: a9d4f7c2e1b8
Revises: e3b8d1a6f204
Create Date: 2026-10-14 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4f7c2e1b8"
down_revision: str | Sequence[str] | None = "e3b8d1a6f204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("assistant_conversations", "last_model_id"),
    ("assistant_messages", "model_id"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_{column}_llm_models"),
            table,
            "llm_models",
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_constraint(op.f(f"fk_{table}_{column}_llm_models"), table, type_="foreignkey")
        op.drop_column(table, column)
