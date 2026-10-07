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
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Route

from dawam.modules.auth import AuthService, Invitations
from dawam.modules.jobs import InlineJobRunner, JobService, QueuedJobRunner
from dawam.modules.llm import FakeAdapter
from dawam.modules.mail import MailService
from dawam.modules.sources import EXTRACT_JOB, SnapshotService
from dawam.modules.sources.internal.connector import ColumnInfo, SourceCatalog, TableInfo
from dawam.modules.workspaces import WorkspaceService
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
    files: Body | None = field(default=None, kw_only=True)
    """Multipart form files (``{"file": (name, bytes, type)}``) for an upload route."""
    query: Mapping[str, str] | None = field(default=None, kw_only=True)
    """Query parameters a route requires."""
    setup: Callable[[RoleClients], object] | None = field(default=None, kw_only=True)
    """Run before the request, for state an allowed role needs to succeed (e.g. a
    second owner, so the owner may leave)."""

    path_params: Mapping[str, Callable[[RoleClients], object]] = field(
        default_factory=dict, kw_only=True
    )
    """Providers for this row's path parameters that replace the shared ``PATH_PARAMS``
    (e.g. a Workspace the row needs in a particular state)."""

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


def kpi_id(roles: RoleClients) -> uuid.UUID:
    """A KPI of the Workspace (the owner adds it through the API once)."""
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/kpis"
    listed = owner.get(path).json()["items"]
    if listed:
        return uuid.UUID(listed[0]["id"])
    response = owner.post(path, json={"name": "Customer count"})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def comment_id(roles: RoleClients) -> uuid.UUID:
    """A thread's first comment (the owner adds it through the API once)."""
    owner = roles.client("owner")
    response = owner.post(
        f"/api/v1/workspaces/{roles.workspace_id}/comments",
        json={"object_type": "kpi", "object_id": str(kpi_id(roles)), "body": "Net of fees?"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def file_id(roles: RoleClients) -> uuid.UUID:
    """A document in the Source System's file area (the owner uploads it once)."""
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}/files"
    listed = owner.get(path).json()["items"]
    if listed:
        return uuid.UUID(listed[0]["id"])
    response = owner.post(path, files={"file": ("sad.md", b"# SAD", "text/markdown")})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def source_link_id(roles: RoleClients) -> uuid.UUID:
    """A link of the Source System (a fresh one per request, so deleting it in one
    request does not affect the next)."""
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}/links",
        json={"kind": "jira", "title": "CBS-1", "url": "https://jira.example.com/browse/CBS-1"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def source_table_id(roles: RoleClients) -> uuid.UUID:
    """A table of the Source System's Snapshot (the stand-in source's one table)."""
    snapshot = snapshot_id(roles)
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    content = roles.client("owner").get(f"{system}/snapshots/{snapshot}").json()
    return uuid.UUID(content["tables"][0]["id"])


def linked_file_id(roles: RoleClients) -> uuid.UUID:
    """A document already linked to the Source System's table (set up through the API
    once), so unlinking it succeeds for an allowed role."""
    owner = roles.client("owner")
    file = file_id(roles)
    table = source_table_id(roles)
    response = owner.post(
        f"/api/v1/workspaces/{roles.workspace_id}/files/{file}/object-links",
        json={"object_type": "table", "object_id": str(table)},
    )
    assert response.status_code in (200, 201), response.text
    return file


def job_id(roles: RoleClients) -> uuid.UUID:
    """A queued job the Workspace's owner started, made through the jobs module (a fresh
    one per request, so cancelling it in one request does not affect the next)."""
    state = roles.app.state
    runner = QueuedJobRunner()
    runner.register("noop", lambda params, ctx: None)
    jobs = JobService(state.engine, runner=runner, clock=state.services.clock)
    return jobs.submit(
        roles.workspace_id,
        "noop",
        {},
        title="Permission matrix job",
        created_by=roles.user("owner").id,
    ).id


def provider_id(roles: RoleClients) -> uuid.UUID:
    """An LLM provider the admin registers through the API (a fresh one per request, so
    deleting it in one request does not affect the next)."""
    response = roles.client("admin").post(
        "/api/v1/admin/llm/providers",
        json={
            "name": f"Provider {uuid.uuid4()}",
            "base_url": "http://llm.test/v1",
            "internal": True,
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def model_id(roles: RoleClients) -> uuid.UUID:
    """A model of a fresh LLM provider, registered through the API."""
    response = roles.client("admin").post(
        f"/api/v1/admin/llm/providers/{provider_id(roles)}/models",
        json={"name": "qwen3", "roles": ["agent"]},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def tested_model_id(roles: RoleClients) -> uuid.UUID:
    """A model that passed "Test connection" (against the scripted fake)."""
    use_fake_llm(roles)
    model = model_id(roles)
    response = roles.client("admin").post(f"/api/v1/admin/llm/models/{model}/test")
    assert response.json()["test_ok"] is True, response.text
    return model


def use_fake_llm(roles: RoleClients) -> None:
    """Answer every LLM request with the scripted fake, so "Test connection" needs no server."""
    fake = FakeAdapter()
    roles.app.state.services.llm_adapters = lambda kind, config: fake


class _OneTableSource:
    """A stand-in source database with one table, for ``snapshot_id``; its ``national_id``
    column gets a PII finding."""

    def extract(self) -> SourceCatalog:
        column = ColumnInfo("id", 1, "integer", False, True, None, None)
        national = ColumnInfo("national_id", 2, "text", True, False, None, None)
        table = TableInfo("public", "accounts", "table", 1, None, None, (column, national))
        return SourceCatalog(tables=(table,), routines=(), schemas=("public",))


def snapshot_id(roles: RoleClients) -> uuid.UUID:
    """A Snapshot of the Source System: the owner gives it a Connection through the API,
    then an extraction runs (through the sources module) against a stand-in source."""
    owner = roles.client("owner")
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    listed = owner.get(f"{system}/snapshots").json()["items"]
    if listed:
        return uuid.UUID(listed[0]["id"])
    response = owner.put(
        f"{system}/connection",
        json={
            "host": "127.0.0.1",
            "port": 1,
            "database": "source",
            "username": "reader",
            "allowed_schemas": ["public"],
        },
    )
    assert response.status_code in (200, 201), response.text
    state = roles.app.state
    clock = state.services.clock
    runner = InlineJobRunner()
    snapshots = SnapshotService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=runner,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        connectors=lambda engine, params: _OneTableSource(),  # type: ignore[arg-type,return-value]
    )
    runner.register(EXTRACT_JOB, snapshots.run_extraction)
    snapshots.start_extraction(roles.user("owner"), roles.workspace_id, system_id(roles))
    [snapshot] = snapshots.list(roles.user("owner"), roles.workspace_id, system_id(roles))
    return snapshot.id


def table_id(roles: RoleClients) -> str:
    """The extracted stand-in table's Source Object id."""
    snapshot_id(roles)
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    return roles.client("owner").get(f"{system}/schema").json()["tables"][0]["id"]


def column_id(roles: RoleClients) -> str:
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    return roles.client("owner").get(f"{system}/schema").json()["tables"][0]["columns"][0]["id"]


def ensure_data_warehouse(roles: RoleClients) -> None:
    """Sets the Data Warehouse up through the API unless it already is."""
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"
    if not owner.get(path).json()["set_up"]:
        response = owner.post(path, json={"target_platform": "postgresql"})
        assert response.status_code == 201, response.text


def dw_table_id(roles: RoleClients) -> str:
    """A fact in the Core model (the owner adds it once, after setting the warehouse up)."""
    ensure_data_warehouse(roles)
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse/tables"
    listed = owner.get(path, params={"layer": "core"}).json()["items"]
    if listed:
        return listed[0]["id"]
    response = owner.post(
        path,
        json={
            "layer": "core",
            "name": "fact_sales",
            "kind": "fact",
            "grain": "One row per order line",
            "fact_type": "transactional",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def dw_column_id(roles: RoleClients) -> str:
    """A measure of that fact."""
    table = dw_table_id(roles)
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse/tables/{table}"
    columns = owner.get(path).json()["columns"]
    if columns:
        return columns[0]["id"]
    response = owner.post(
        f"{path}/columns",
        json={"name": "amount", "data_type": {"type": "integer"}, "role": "measure"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def staging_table_name(roles: RoleClients) -> str:
    """A Staging Table for a Core branch to read (inserted directly: only source analysis
    makes them)."""
    table = dw_table_id(roles)
    now = datetime.now(UTC)
    with roles.app.state.engine.begin() as connection:
        if not connection.execute(
            sa.text("select 1 from dw_tables where layer = 'staging' and name = 'stg_crm'")
        ).first():
            connection.execute(
                sa.text(
                    "insert into dw_tables (id, data_warehouse_id, layer, name, kind,"
                    " is_aggregate, is_conformed, description, created_at, updated_at, version)"
                    " select :id, data_warehouse_id, 'staging', 'stg_crm', 'staging', false,"
                    " false, '', :now, :now, 1 from dw_tables where id = :t"
                ),
                {"id": uuid.uuid4(), "t": table, "now": now},
            )
    return "stg_crm"


def branch_id(roles: RoleClients) -> str:
    """A branch of that fact's mapping, inserted directly."""
    table = dw_table_id(roles)
    staging_table_name(roles)
    with roles.app.state.engine.begin() as connection:
        found = connection.execute(
            sa.text(
                "select b.id from mapping_branches b join table_mappings m on m.id = "
                "b.table_mapping_id where m.dw_table_id = :t"
            ),
            {"t": table},
        ).first()
        if found:
            return str(found[0])
        mapping_id, new_id, now = uuid.uuid4(), uuid.uuid4(), datetime.now(UTC)
        connection.execute(
            sa.text(
                "insert into table_mappings (id, dw_table_id, match_keys, notes, created_at,"
                " updated_at, version) values (:m, :t, '[]', '', :now, :now, 0)"
            ),
            {"m": mapping_id, "t": table, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into mapping_branches (id, table_mapping_id, ordinal, name,"
                " driving_input, joins, filters, created_at, updated_at, version) values"
                " (:b, :m, 1, 'CRM', 'stg_crm', '', '', :now, :now, 1)"
            ),
            {"b": new_id, "m": mapping_id, "now": now},
        )
    return str(new_id)


def pii_finding_id(roles: RoleClients) -> str:
    """The name-rule finding on the stand-in table's ``national_id`` column."""
    snapshot_id(roles)
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    return roles.client("owner").get(f"{system}/pii-findings").json()["items"][0]["id"]


def pii_rule_id(roles: RoleClients) -> str:
    """A fresh custom PII rule (so deleting one does not affect the next request)."""
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/pii-rules",
        json={
            "name": f"r_{uuid.uuid4().hex[:12]}",
            "keywords": ["emp_no"],
            "category": "direct_identifier",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def rename_pair(roles: RoleClients) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A rename candidate and its removed and added column, written straight into the
    stand-in table (fresh per request, so confirming one does not affect the next):
    ``(candidate_id, removed_id, added_id)``."""
    snapshot = snapshot_id(roles)
    table = uuid.UUID(table_id(roles))
    removed, added, candidate = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    column = sa.text(
        "INSERT INTO src_columns (id, table_id, name, current_definition, status, version) "
        "VALUES (:id, :table, :name, '{}', :status, 1)"
    )
    with roles.app.state.engine.begin() as conn:
        conn.execute(
            column, {"id": removed, "table": table, "name": "old", "status": "source_removed"}
        )
        conn.execute(column, {"id": added, "table": table, "name": "new", "status": "present"})
        conn.execute(
            sa.text(
                "INSERT INTO rename_candidates (id, snapshot_id, object_type, old_object_id, "
                "new_object_id, new_name, confidence, status) VALUES (:id, :snapshot, 'column', "
                ":old, :new, 'new', 0.9, 'suggested')"
            ),
            {"id": candidate, "snapshot": snapshot, "old": removed, "new": added},
        )
    return candidate, removed, added


def relationship_id(roles: RoleClients) -> str:
    """A suggested relationship between the stand-in table's two columns, written straight
    into the table (reset to ``suggested`` on every request)."""
    snapshot_id(roles)
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id(roles)}"
    columns = roles.client("owner").get(f"{system}/schema").json()["tables"][0]["columns"]
    ids = {c["name"]: c["id"] for c in columns}
    with roles.app.state.engine.begin() as conn:
        return str(
            conn.execute(
                sa.text(
                    "INSERT INTO relationships (id, from_column_id, to_column_id, origin, "
                    "confidence, evidence, status, version, detected_at) VALUES (:id, :f, :t, "
                    "'inferred', 0.8, '{}', 'suggested', 1, now()) "
                    "ON CONFLICT (from_column_id, to_column_id) DO UPDATE SET status = 'suggested' "
                    "RETURNING id"
                ),
                {"id": uuid.uuid4(), "f": ids["national_id"], "t": ids["id"]},
            ).scalar_one()
        )


def conversation_id(roles: RoleClients) -> uuid.UUID:
    """An assistant conversation the owner started and shared with the Workspace, so every
    member may read it while only the owner may post to it."""
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/assistant/conversations"
    created = owner.post(path, json={})
    assert created.status_code == 201, created.text
    shared = owner.patch(f"{path}/{created.json()['id']}", json={"shared_with_workspace": True})
    assert shared.status_code == 200, shared.text
    return uuid.UUID(created.json()["id"])


PATH_PARAMS: dict[str, Callable[[RoleClients], object]] = {
    "conversation_id": conversation_id,
    "candidate_id": lambda roles: rename_pair(roles)[0],
    "workspace_id": lambda roles: roles.workspace_id,
    "link_id": undelivered_link_id,
    "user_id": lambda roles: roles.user("non_member").id,
    "invitation_id": invitation_id,
    "member_id": colleague_id,
    "system_id": system_id,
    "job_id": job_id,
    "notification_id": lambda roles: uuid.uuid4(),
    "kpi_id": kpi_id,
    "comment_id": comment_id,
    "file_id": file_id,
    "snapshot_id": snapshot_id,
    "source_link_id": source_link_id,
    "object_type": lambda roles: "table",
    "object_id": source_table_id,
    "table_id": table_id,
    "column_id": column_id,
    "dw_table_id": dw_table_id,
    "dw_column_id": dw_column_id,
    "branch_id": branch_id,
    "finding_id": pii_finding_id,
    "relationship_id": relationship_id,
    "rule_id": pii_rule_id,
    "provider_id": provider_id,
    "model_id": model_id,
}
"""How to fill each path parameter. Add one when a route introduces a new name."""


def request_path(row: Row, roles: RoleClients) -> str:
    names = [name for _, name, _, _ in string.Formatter().parse(row.path) if name]
    providers = {**PATH_PARAMS, **row.path_params}
    unknown = [name for name in names if name not in providers]
    if unknown:
        raise AssertionError(
            f"{row}: no provider for path parameter(s) {unknown}; add them to "
            "PATH_PARAMS in tests/authz/matrix.py"
        )
    return row.path.format(**{name: providers[name](roles) for name in names})


def send(row: Row, role: Role, roles: RoleClients) -> Any:
    client = roles.client(role)
    if row.setup is not None:
        row.setup(roles)
    path = request_path(row, roles)
    json = row.json(roles) if row.json is not None else None
    files = row.files(roles) if row.files is not None else None
    return client.request(row.method, path, json=json, files=files, params=row.query)


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
