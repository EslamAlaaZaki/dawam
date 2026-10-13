"""Messages saved before senders were recorded get their conversation's starter."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command

from dawam.platform.migrations import alembic_config

BEFORE = "b4e8c1d7a2f5"
AFTER = "5d1a8e3c7f42"


def test_existing_member_messages_are_backfilled_with_the_starter(monkeypatch, fresh_database_url):
    monkeypatch.setenv("DAWAM_DATABASE_URL", fresh_database_url)
    command.upgrade(alembic_config(), BEFORE)
    starter, workspace, conversation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    question, answer = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(fresh_database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, display_name, password_hash, system_role, "
                    "created_at) VALUES (:id, 'starter@example.com', 'S', 'x', 'user', now())"
                ),
                {"id": starter},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO workspaces (id, name, description, domain, created_by, "
                    "created_at, updated_at, version) "
                    "VALUES (:id, 'W', '', '', :user, now(), now(), 1)"
                ),
                {"id": workspace, "user": starter},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO workspace_members (workspace_id, user_id, role, added_at) "
                    "VALUES (:workspace, :user, 'owner', now())"
                ),
                {"workspace": workspace, "user": starter},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO assistant_conversations (id, workspace_id, user_id, title, "
                    "shared_with_workspace, created_at, updated_at) "
                    "VALUES (:id, :workspace, :user, 'T', false, now(), now())"
                ),
                {"id": conversation, "workspace": workspace, "user": starter},
            )
            for message, position, role in ((question, 1, "user"), (answer, 2, "assistant")):
                conn.execute(
                    sa.text(
                        "INSERT INTO assistant_messages (id, conversation_id, position, role, "
                        "content, created_at) VALUES (:id, :conversation, :position, :role, "
                        "'x', now())"
                    ),
                    {
                        "id": message,
                        "conversation": conversation,
                        "position": position,
                        "role": role,
                    },
                )

        command.upgrade(alembic_config(), AFTER)

        with engine.connect() as conn:
            senders = dict(
                conn.execute(sa.text("SELECT id, sender_user_id FROM assistant_messages")).all()
            )
        assert senders == {question: starter, answer: None}
    finally:
        engine.dispose()
