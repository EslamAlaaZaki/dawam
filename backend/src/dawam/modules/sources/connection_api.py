"""``.../systems/{system_id}/connection``: a Source System's live database Connection.

Owners only. No response ever carries the password: it reports ``has_password``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .connection_service import (
    DEFAULT_PORTS,
    PASSWORD_MAX_LENGTH,
    ConnectionInput,
    ConnectionService,
)
from .connection_service import (
    Connection as ConnectionView,
)
from .internal.connector import ConnectionTest
from .tables import HOST_MAX_LENGTH, IDENTIFIER_MAX_LENGTH

router = APIRouter(tags=["sources"])


def connection_service(request: Request) -> ConnectionService:
    state = request.app.state
    clock = state.services.clock
    return ConnectionService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
    )


ConnectionServiceDep = Annotated[ConnectionService, Depends(connection_service)]


class Connection(BaseModel):
    id: uuid.UUID
    source_system_id: uuid.UUID
    engine: Literal["postgresql", "sqlserver", "mysql", "oracle"]
    host: str
    port: int
    database: str
    username: str
    has_password: bool = Field(description="Whether a password is stored. It is never returned.")
    options: dict[str, Any]
    allowed_schemas: list[str] = Field(
        description="The Database Schemas DAWAM may read; nothing outside them is ever queried."
    )
    can_write: bool | None = Field(
        description="From the last test of these settings: the user can change data or objects "
        "(show a warning). Null: not tested since the last change."
    )
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConnectionRequest(BaseModel):
    engine: Literal["postgresql", "sqlserver", "mysql", "oracle"] = "postgresql"
    host: str = Field(max_length=HOST_MAX_LENGTH * 2)
    port: int = Field(
        default=DEFAULT_PORTS["postgresql"],
        ge=1,
        le=65535,
        description="Default 5432 (PostgreSQL); MySQL/MariaDB usually listens on 3306, "
        "Oracle on 1521.",
    )
    database: str = Field(
        max_length=IDENTIFIER_MAX_LENGTH * 2,
        description="The database name; for Oracle, the service name. Oracle Database Schemas "
        "are user names, given as Oracle stores them (usually upper case).",
    )
    username: str = Field(max_length=IDENTIFIER_MAX_LENGTH * 2)
    password: str | None = Field(
        default=None,
        max_length=PASSWORD_MAX_LENGTH * 2,
        description="Leave out to keep the stored password (or, for a test, to use it); "
        "allowed only with the host, port, database and username it was saved for, "
        "otherwise 422 `password_required`. An empty string clears it.",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="`sslmode`, `connect_timeout` (1-60) and `statement_timeout_seconds` "
        "(1-600, default 30).",
    )
    allowed_schemas: list[str] = Field(
        default_factory=lambda: ["public"],
        max_length=500,
        description="The Database Schemas DAWAM may read (at least one).",
    )


class ConnectionTestResult(BaseModel):
    ok: bool
    error_code: str | None = Field(description="A stable code when `ok` is false.")
    error: str | None = Field(description="A safe message: no host, user or password in it.")
    server_version: str | None
    can_write: bool | None = Field(
        description="True if the user can change data or objects: DAWAM only reads, "
        "so give it a read-only user."
    )
    available_schemas: list[str] = Field(description="Database Schemas to pick from.")
    missing_schemas: list[str] = Field(description="Allowed schemas that were not found.")


def _input(body: ConnectionRequest) -> ConnectionInput:
    return ConnectionInput(
        engine=body.engine,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        password=body.password,
        options=body.options,
        allowed_schemas=body.allowed_schemas,
    )


def _out(view: ConnectionView) -> Connection:
    return Connection.model_validate(view, from_attributes=True)


@router.get("", operation_id="getConnection")
def get_connection(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    connections: ConnectionServiceDep,
) -> Connection:
    """The Source System's Connection (owners only, archived Workspaces too). 404
    `connection_not_found` if none."""
    return _out(connections.get(user, workspace_id, system_id))


@router.put("", operation_id="saveConnection")
def save_connection(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: ConnectionRequest,
    response: Response,
    user: CurrentUser,
    connections: ConnectionServiceDep,
) -> Connection:
    """Create or replace the Connection (owners only; 201 when created). The password is
    sealed before it is stored. 422 `invalid_connection` for bad settings;
    `password_required` to reuse the stored password for another endpoint."""
    view, created = connections.save(user, workspace_id, system_id, _input(body))
    if created:
        response.status_code = 201
    return _out(view)


@router.post("/test", operation_id="testConnection")
def test_connection(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: ConnectionRequest,
    user: CurrentUser,
    connections: ConnectionServiceDep,
) -> ConnectionTestResult:
    """Try these settings without saving them (owners only). Always 200: a failure is
    `ok: false` with a safe `error`. Without a password the stored one is used, for the
    endpoint it was saved for only (422 `password_required` otherwise)."""
    result: ConnectionTest = connections.test(user, workspace_id, system_id, _input(body))
    return ConnectionTestResult(
        ok=result.ok,
        error_code=result.error_code,
        error=result.error,
        server_version=result.server_version,
        can_write=result.can_write,
        available_schemas=list(result.available_schemas),
        missing_schemas=list(result.missing_schemas),
    )
