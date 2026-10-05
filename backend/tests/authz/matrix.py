"""Machinery of the permission suite (``test_permission_matrix.py``); the rows live there.

A ``Row`` is one endpoint (method + route path, exactly as FastAPI registered it)
and the outcome every role must get, written from the spec's permission matrix
(§4.3), never from the code's policy. Outcomes are status classes: ``ALLOWED`` (any
2xx) or a denial (401, 403, 404).

To build expectations, use the helper that matches the endpoint:

- ``public()``: everyone, signed in or not;
- ``signed_in()``: every signed-in user (anonymous gets 401);
- ``admin_only()``: system administration; admins only (other users get 403);
- ``workspace(admin=, owner=, editor=, viewer=)``: a Workspace's content, with the
  four columns of the §4.3 matrix row copied as they are. Anonymous gets 401; a
  non-member user, and a non-member admin where the matrix says ❌, get 404 (the
  Workspace's existence is not leaked); a member whose role is ❌ gets 403.

Path parameters are filled by ``PATH_PARAMS`` (name -> function of ``RoleClients``),
which may create what the parameter points at. A ticket whose routes take a new
parameter (e.g. ``{system_id}``) adds a provider there.
"""

from __future__ import annotations

import string
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Route

from dawam.modules.auth import AuthService, Invitations
from dawam.modules.mail import MailService
from dawam.platform.email import EmailMessage, OneTimeLink
from tests.roles import PASSWORD, ROLES, Role, RoleClients

try:
    from fastapi.routing import iter_route_contexts
except ImportError:  # FastAPI before lazily included routers, whose app.routes is flat

    def iter_route_contexts(routes: Any) -> Any:  # type: ignore[no-redef]
        return iter(routes)


class Outcome(Enum):
    ALLOWED = "allowed (2xx)"
    UNAUTHENTICATED = "401"
    FORBIDDEN = "403"
    NOT_FOUND = "404"


ALLOWED = Outcome.ALLOWED
UNAUTHENTICATED = Outcome.UNAUTHENTICATED
FORBIDDEN = Outcome.FORBIDDEN
NOT_FOUND = Outcome.NOT_FOUND

Expectations = Mapping[Role, Outcome]


def outcome_of(status_code: int) -> Outcome | None:
    """The outcome a status code stands for; ``None`` for anything else (e.g. 422)."""
    if 200 <= status_code < 300:
        return ALLOWED
    return {401: UNAUTHENTICATED, 403: FORBIDDEN, 404: NOT_FOUND}.get(status_code)


def public() -> Expectations:
    return dict.fromkeys(ROLES, ALLOWED)


def signed_in() -> Expectations:
    return {**dict.fromkeys(ROLES, ALLOWED), "anonymous": UNAUTHENTICATED}


def admin_only() -> Expectations:
    """System administration (§4.1): admins only, member of any Workspace or not."""
    return {
        **dict.fromkeys(ROLES, FORBIDDEN),
        "anonymous": UNAUTHENTICATED,
        "admin": ALLOWED,
    }


def workspace(*, admin: bool, owner: bool, editor: bool, viewer: bool) -> Expectations:
    """One row of the §4.3 matrix: ``admin`` is its "Admin (non-member)" column."""
    return {
        "anonymous": UNAUTHENTICATED,
        "non_member": NOT_FOUND,
        "viewer": ALLOWED if viewer else FORBIDDEN,
        "editor": ALLOWED if editor else FORBIDDEN,
        "owner": ALLOWED if owner else FORBIDDEN,
        "admin": ALLOWED if admin else NOT_FOUND,
    }


Body = Callable[[RoleClients], Any]


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    """The route path as registered, e.g. ``/api/v1/workspaces/{workspace_id}``."""
    matrix: str
    """What it checks: the §4.3 matrix action, or why the endpoint is public."""
    expect: Expectations
    json: Body | None = field(default=None, kw_only=True)
    """A request body that would succeed for an allowed role."""
    setup: Callable[[RoleClients], object] | None = field(default=None, kw_only=True)
    """Run before the request, for state an allowed role needs to succeed (e.g. a
    second owner, so the owner may leave)."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def credentials_of(role: Role) -> Body:
    """A sign-in body for ``role``'s user."""
    return lambda roles: {"email": roles.user(role).email, "password": PASSWORD}


def undelivered_link_id(roles: RoleClients) -> uuid.UUID:
    """A link kept for admins (SMTP is off in the suite), made through the mail module."""
    app = roles.app
    mail = MailService(
        app.state.engine,
        app.state.settings,
        sender=app.state.services.email,
        clock=app.state.services.clock,
    )
    now = app.state.services.clock()
    mail.send(
        EmailMessage(to="someone@example.com", subject="Join", body="..."),
        link=OneTimeLink(url="http://x/l", purpose="invitation", expires_at=now + timedelta(1)),
    )
    return mail.undelivered_links()[0].id


def invitation_id(roles: RoleClients) -> uuid.UUID:
    """A pending invitation, made through the auth module (its link is kept for admins)."""
    state = roles.app.state
    invitations = Invitations(
        state.engine, state.settings, mailer=state.mailer, clock=state.services.clock
    )
    return invitations.invite("invitee@example.com", actor_id=roles.user("admin").id).invitation.id


COLLEAGUE_EMAIL = "colleague@example.com"


def colleague_id(roles: RoleClients) -> uuid.UUID:
    """A viewer of the Workspace who is none of the roles, for members to act on; the
    owner adds them through the API."""
    state = roles.app.state
    auth = AuthService(state.engine, state.settings, clock=state.services.clock)
    colleague = auth.find_user_by_email(COLLEAGUE_EMAIL)
    if colleague is None:
        colleague = auth.create_user(
            email=COLLEAGUE_EMAIL, password=PASSWORD, display_name="Colleague", system_role="user"
        )
        response = roles.client("owner").post(
            f"/api/v1/workspaces/{roles.workspace_id}/members",
            json={"email": COLLEAGUE_EMAIL, "role": "viewer"},
        )
        assert response.status_code == 201, response.text
    return colleague.id


def system_id(roles: RoleClients) -> uuid.UUID:
    """A Source System of the Workspace (the owner adds it through the API once)."""
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/systems"
    listed = owner.get(path).json()["items"]
    if listed:
        return uuid.UUID(listed[0]["id"])
    response = owner.post(path, json={"name": "Core Banking", "code": "cbs"})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


PATH_PARAMS: dict[str, Callable[[RoleClients], object]] = {
    "workspace_id": lambda roles: roles.workspace_id,
    "link_id": undelivered_link_id,
    "user_id": lambda roles: roles.user("non_member").id,
    "invitation_id": invitation_id,
    "member_id": colleague_id,
    "system_id": system_id,
}
"""How to fill each path parameter. Add one when a route introduces a new name."""


def request_path(row: Row, roles: RoleClients) -> str:
    names = [name for _, name, _, _ in string.Formatter().parse(row.path) if name]
    unknown = [name for name in names if name not in PATH_PARAMS]
    if unknown:
        raise AssertionError(
            f"{row}: no provider for path parameter(s) {unknown}; add them to "
            "PATH_PARAMS in tests/authz/matrix.py"
        )
    return row.path.format(**{name: PATH_PARAMS[name](roles) for name in names})


def send(row: Row, role: Role, roles: RoleClients) -> Any:
    client = roles.client(role)
    if row.setup is not None:
        row.setup(roles)
    path = request_path(row, roles)
    json = row.json(roles) if row.json is not None else None
    return client.request(row.method, path, json=json)


_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app serves, found by walking its routes."""
    found: set[tuple[str, str]] = set()
    for route in iter_route_contexts(app.routes):
        original: BaseRoute = getattr(route, "original_route", route)
        if not isinstance(original, APIRoute | Route):
            raise AssertionError(
                f"{original!r} is not a plain route, so the permission suite cannot list "
                "its endpoints; teach registered_routes in tests/authz/matrix.py about it"
            )
        for method in (route.methods or set()) - _IGNORED_METHODS:
            found.add((method, route.path))
    return found


def describe(keys: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"  {method} {path}" for method, path in sorted(keys))
