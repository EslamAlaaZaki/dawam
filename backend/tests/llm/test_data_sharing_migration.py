"""Workspaces keep the access their cumulative data-sharing level gave them."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command

from dawam.platform.migrations import alembic_config

BEFORE = "c5d8e2a1f347"
AFTER = "d7a3f5b2c618"

EXPECTED = {
    "metadata": (False, False, False),
    "profiles": (True, False, False),
    "documents": (True, True, False),
    "samples": (True, True, True),
}


def test_each_level_becomes_the_kinds_it_included(monkeypatch, fresh_database_url):
    monkeypatch.setenv("DAWAM_DATABASE_URL", fresh_database_url)
    command.upgrade(alembic_config(), BEFORE)
    user = uuid.uuid4()
    workspaces = {level: uuid.uuid4() for level in EXPECTED}
    engine = sa.create_engine(fresh_database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, display_name, password_hash, system_role, "
                    "created_at) VALUES (:id, 'u@example.com', 'U', 'x', 'user', now())"
                ),
                {"id": user},
            )
            for level, workspace in workspaces.items():
                conn.execute(
                    sa.text(
                        "INSERT INTO workspaces (id, name, description, domain, created_by, "
                        "created_at, updated_at, version) "
                        "VALUES (:id, :name, '', '', :user, now(), now(), 1)"
                    ),
                    {"id": workspace, "name": level, "user": user},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO workspace_members (workspace_id, user_id, role, added_at) "
                        "VALUES (:id, :user, 'owner', now())"
                    ),
                    {"id": workspace, "user": user},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO llm_workspace_settings (workspace_id, internal_only, "
                        "data_sharing_level, auto_retrieve, updated_at) "
                        "VALUES (:id, false, :level, false, now())"
                    ),
                    {"id": workspace, "level": level},
                )

        command.upgrade(alembic_config(), AFTER)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT workspace_id, share_profiles, share_documents, share_samples "
                    "FROM llm_workspace_settings"
                )
            ).all()
        got = {r[0]: tuple(r[1:]) for r in rows}
        assert got == {workspaces[level]: kinds for level, kinds in EXPECTED.items()}
    finally:
        engine.dispose()
