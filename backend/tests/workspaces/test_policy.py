"""The policy's public interface: ``can`` and ``WorkspaceService.authorize`` (spec §6.2).

Other modules check their Workspace-scoped resources through ``authorize``, passing
the ``workspace_id`` stored on the resource; the permission suite covers the HTTP side.
"""

from __future__ import annotations

import uuid

import pytest

from dawam.modules.workspaces import (
    INSTALLATION,
    Action,
    WorkspaceScope,
    WorkspaceService,
    can,
)
from dawam.platform.errors import ApiError
from tests.roles import RoleClients


@pytest.fixture
def workspaces(app, clock) -> WorkspaceService:
    return WorkspaceService(app.state.engine, clock=clock)


def test_authorize_returns_the_scope_with_the_users_role(
    roles: RoleClients, workspaces: WorkspaceService
):
    roles.client("editor")

    scope = workspaces.authorize(roles.user("editor"), Action.VIEW_WORKSPACE, roles.workspace_id)

    assert scope == WorkspaceScope(
        workspace_id=roles.workspace_id,
        user_id=roles.user("editor").id,
        role="editor",
        archived=False,
    )


@pytest.mark.parametrize(
    ("role", "action", "status"),
    [
        ("viewer", Action.EDIT_WORKSPACE, 403),
        ("editor", Action.MANAGE_MEMBERS, 403),
        ("non_member", Action.VIEW_WORKSPACE, 404),
        ("admin", Action.VIEW_WORKSPACE, 404),
    ],
)
def test_authorize_refuses_with_403_for_members_and_404_for_everyone_else(
    roles: RoleClients, workspaces: WorkspaceService, role, action, status
):
    roles.client(role)

    with pytest.raises(ApiError) as refused:
        workspaces.authorize(roles.user(role), action, roles.workspace_id)

    assert refused.value.status_code == status


def test_authorize_says_404_for_a_workspace_that_does_not_exist(
    roles: RoleClients, workspaces: WorkspaceService
):
    with pytest.raises(ApiError) as refused:
        workspaces.authorize(roles.user("owner"), Action.VIEW_WORKSPACE, uuid.uuid4())

    assert refused.value.status_code == 404


def test_can_rejects_a_resource_of_the_wrong_kind_or_for_another_user(roles: RoleClients):
    owner = roles.user("owner")
    scope = WorkspaceScope(
        workspace_id=uuid.uuid4(), user_id=owner.id, role="owner", archived=False
    )

    assert can(owner, Action.CREATE_WORKSPACE, INSTALLATION)
    with pytest.raises(TypeError):
        can(owner, Action.CREATE_WORKSPACE, scope)
    with pytest.raises(TypeError):
        can(owner, Action.EDIT_WORKSPACE, INSTALLATION)
    with pytest.raises(ValueError):
        can(roles.user("viewer"), Action.VIEW_WORKSPACE, scope)


def test_only_admins_may_manage_email(roles: RoleClients):
    assert can(roles.user("admin"), Action.MANAGE_EMAIL, INSTALLATION)
    assert not can(roles.user("owner"), Action.MANAGE_EMAIL, INSTALLATION)
