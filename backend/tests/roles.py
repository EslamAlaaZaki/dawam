"""A client for each role of the permission matrix (spec §4.3, §10), on one Workspace.

``RoleClients`` (the ``roles`` fixture) creates what a role needs only when it is
first asked for:

- ``roles.client(role)``: a test client acting as ``role``, sending the CSRF token on
  every request. ``anonymous`` has no session; every other role is a user signed in
  through the API.
- ``roles.user(role)``: that role's user (not for ``anonymous``).
- ``roles.workspace``: the Workspace the roles refer to, as ``POST /workspaces``
  returned it to its owner (who created it). ``viewer`` and ``editor`` are made
  members of it; ``non_member`` (a regular user) and ``admin`` (a system admin) are
  not.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from typing import Any, Literal, get_args

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dawam.modules.auth import AuthService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token, sign_in

Role = Literal["anonymous", "non_member", "viewer", "editor", "owner", "admin"]
ROLES: tuple[Role, ...] = get_args(Role)

PASSWORD = "permission matrix"


class RoleClients:
    def __init__(
        self, app: FastAPI, auth: AuthService, workspaces: WorkspaceService, stack: ExitStack
    ) -> None:
        self._app = app
        self._auth = auth
        self._workspaces = workspaces
        self._stack = stack
        self._users: dict[Role, Any] = {}
        self._clients: dict[Role, TestClient] = {}
        self._workspace: dict[str, Any] | None = None

    def user(self, role: Role) -> Any:
        """The ``dawam.modules.auth.User`` acting as ``role``."""
        if role == "anonymous":
            raise ValueError("the anonymous role has no user")
        if role not in self._users:
            self._users[role] = self._auth.create_user(
                email=f"{role.replace('_', '-')}@example.com",
                password=PASSWORD,
                display_name=role.replace("_", " ").title(),
                system_role="admin" if role == "admin" else "user",
            )
        return self._users[role]

    @property
    def app(self) -> FastAPI:
        return self._app

    @property
    def workspace(self) -> dict[str, Any]:
        if self._workspace is None:
            response = self.client("owner").post(
                "/api/v1/workspaces",
                json={"name": "Permission Matrix", "description": "", "domain": "Testing"},
            )
            assert response.status_code == 201, response.text
            self._workspace = response.json()
        return self._workspace

    @property
    def workspace_id(self) -> uuid.UUID:
        return uuid.UUID(self.workspace["id"])

    def client(self, role: Role) -> TestClient:
        if role not in self._clients:
            client = self._stack.enter_context(TestClient(self._app))
            client.headers[CSRF_HEADER] = csrf_token(client)
            if role != "anonymous":
                user = self.user(role)
                response = sign_in(client, user.email, PASSWORD)
                assert response.status_code == 200, response.text
            self._clients[role] = client
            if role in ("viewer", "editor"):
                self._workspaces.add_member(
                    self.user("owner"), self.workspace_id, member_id=self.user(role).id, role=role
                )
        return self._clients[role]
