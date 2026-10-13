"""record who sent each assistant message (assistant module).

Revision ID: 5d1a8e3c7f42
Revises: b4e8c1d7a2f5
Create Date: 2026-10-13 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d1a8e3c7f42"
down_revision: str | Sequence[str] | None = "b4e8c1d7a2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assistant_messages", sa.Column("sender_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_assistant_messages_sender_user_id_users"),
        "assistant_messages",
        "users",
        ["sender_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Until now only a conversation's starter could post to it, so they sent every member
    # message in it. The assistant's replies have no sender.
    op.execute(
        "UPDATE assistant_messages AS m SET sender_user_id = c.user_id "
        "FROM assistant_conversations AS c "
        "WHERE c.id = m.conversation_id AND m.role = 'user'"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_assistant_messages_sender_user_id_users"), "assistant_messages", type_="foreignkey"
    )
    op.drop_column("assistant_messages", "sender_user_id")
