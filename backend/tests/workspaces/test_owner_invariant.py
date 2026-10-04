"""A Workspace always has at least one owner, backed by the database (spec §6.2).

Deliberate storage-property checks: they write the workspaces module's own tables with
raw SQL, to show the database itself refuses what the service must never do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.roles import RoleClients


@pytest.fixture
def engine(app, roles: RoleClients) -> sa.Engine:
    roles.client("editor")  # the Workspace, its owner and an editor exist
    return app.state.engine


def owner_violation(exc: pytest.ExceptionInfo[IntegrityError]) -> bool:
    diag = exc.value.orig.diag  # type: ignore[union-attr]
    return diag.constraint_name == "workspace_has_owner"


def test_removing_the_last_owner_fails(engine, roles: RoleClients):
    with pytest.raises(IntegrityError) as refused, engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM workspace_members WHERE workspace_id = :w AND role = 'owner'"),
            {"w": roles.workspace_id},
        )

    assert owner_violation(refused)


def test_demoting_the_last_owner_fails(engine, roles: RoleClients):
    with pytest.raises(IntegrityError) as refused, engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE workspace_members SET role = 'editor' WHERE workspace_id = :w"),
            {"w": roles.workspace_id},
        )

    assert owner_violation(refused)


def test_a_workspace_without_an_owner_cannot_be_created(engine, roles: RoleClients):
    with pytest.raises(IntegrityError) as refused, engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workspaces (id, name, description, domain, created_by, "
                "created_at, updated_at, version) "
                "VALUES (:id, 'Orphan', '', '', :by, :now, :now, 1)"
            ),
            {"id": uuid.uuid4(), "by": roles.user("owner").id, "now": datetime.now(UTC)},
        )

    assert owner_violation(refused)


def test_an_owner_may_go_once_another_owner_exists(engine, roles: RoleClients):
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE workspace_members SET role = 'owner' WHERE user_id = :editor"),
            {"editor": roles.user("editor").id},
        )
        conn.execute(
            sa.text("DELETE FROM workspace_members WHERE user_id = :owner"),
            {"owner": roles.user("owner").id},
        )

    response = roles.client("editor").get(f"/api/v1/workspaces/{roles.workspace_id}")
    assert response.json()["role"] == "owner"


def test_deleting_a_workspace_takes_its_members_with_it(engine, roles: RoleClients):
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM workspaces WHERE id = :w"), {"w": roles.workspace_id})

    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM workspace_members")) == 0
