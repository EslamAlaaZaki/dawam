"""Source System Connections: the service API (spec stories 40-43).

Only owners see or change a Connection (spec §4.3). The password is sealed with
``SecretBox`` before it is stored and is never read back out through this API: callers
get ``has_password``. Activity events and error messages carry no host, user or secret.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .internal.connector import (
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    ConnectionParams,
    ConnectionTest,
    Connector,
    ConnectorError,
    connector_for,
)
from .internal.postgres import SSL_MODES
from .tables import HOST_MAX_LENGTH, IDENTIFIER_MAX_LENGTH, ConnectionRecord, SourceSystemRecord

ENGINES = ("postgresql",)
PASSWORD_CONTEXT = "connection.password"
PASSWORD_MAX_LENGTH = 1000
MAX_ALLOWED_SCHEMAS = 100
DEFAULT_PORTS = {"postgresql": 5432}


@dataclass(frozen=True)
class Connection:
    id: uuid.UUID
    source_system_id: uuid.UUID
    engine: str
    host: str
    port: int
    database: str
    username: str
    has_password: bool
    options: dict[str, Any]
    allowed_schemas: list[str]
    can_write: bool | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ConnectionInput:
    """What an owner types. ``password``: ``None`` keeps the stored one, ``""`` clears it."""

    engine: str
    host: str
    port: int
    database: str
    username: str
    password: str | None
    options: dict[str, Any]
    allowed_schemas: list[str]


def _view(record: ConnectionRecord) -> Connection:
    return Connection(
        id=record.id,
        source_system_id=record.source_system_id,
        engine=record.engine,
        host=record.host,
        port=record.port,
        database=record.database,
        username=record.username,
        has_password=record.secret_encrypted is not None,
        options=dict(record.options),
        allowed_schemas=list(record.allowed_schemas),
        can_write=record.can_write,
        last_tested_at=record.last_tested_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_connection", message, {"field": field})


def _text(value: str, field: str, label: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise _invalid(f"The {label} must not be empty.", field)
    if len(cleaned) > max_length:
        raise _invalid(f"The {label} must be at most {max_length} characters.", field)
    return cleaned


def _clean_options(options: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in options.items():
        if (
            (key == "sslmode" and value in SSL_MODES)
            or (
                key == "connect_timeout"
                and isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 60
            )
            or (
                key == "statement_timeout_seconds"
                and isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 600
            )
        ):
            cleaned[key] = value
        else:
            raise _invalid(
                f"The option {key!r} is not supported or has an invalid value. Supported: "
                f"sslmode ({', '.join(SSL_MODES)}), connect_timeout (1-60 s), "
                f"statement_timeout_seconds (1-600, default {DEFAULT_STATEMENT_TIMEOUT_SECONDS}).",
                "options",
            )
    return cleaned


def _clean(data: ConnectionInput) -> ConnectionInput:
    if data.engine not in ENGINES:
        raise _invalid(f"The engine must be one of: {', '.join(ENGINES)}.", "engine")
    if not 1 <= data.port <= 65535:
        raise _invalid("The port must be between 1 and 65535.", "port")
    schemas: list[str] = []
    for name in data.allowed_schemas:
        cleaned = _text(name, "allowed_schemas", "Database Schema name", IDENTIFIER_MAX_LENGTH)
        if cleaned not in schemas:
            schemas.append(cleaned)
    if not schemas:
        raise _invalid(
            "Allow at least one Database Schema; nothing outside the list is ever read.",
            "allowed_schemas",
        )
    if len(schemas) > MAX_ALLOWED_SCHEMAS:
        raise _invalid(
            f"At most {MAX_ALLOWED_SCHEMAS} Database Schemas can be allowed.", "allowed_schemas"
        )
    if data.password is not None and len(data.password) > PASSWORD_MAX_LENGTH:
        raise _invalid(
            f"The password must be at most {PASSWORD_MAX_LENGTH} characters.", "password"
        )
    return ConnectionInput(
        engine=data.engine,
        host=_text(data.host, "host", "host", HOST_MAX_LENGTH),
        port=data.port,
        database=_text(data.database, "database", "database name", IDENTIFIER_MAX_LENGTH),
        username=_text(data.username, "username", "username", IDENTIFIER_MAX_LENGTH),
        password=data.password,
        options=_clean_options(data.options),
        allowed_schemas=schemas,
    )


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Source System not found.")


class ConnectionService:
    """Every method authorizes through the workspaces policy first (owners only)."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        encryption_key: bytes,
        clock: Clock,
        connectors: Callable[[str, ConnectionParams], Connector] = connector_for,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._connectors = connectors

    def get(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Connection:
        """The Connection of a Source System. 404 ``connection_not_found`` if it has none."""
        self._workspaces.authorize(user, Action.MANAGE_CONNECTION, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            record = self._load_connection(db, system_id)
            if record is None:
                raise ApiError(
                    404, "connection_not_found", "This Source System has no Connection yet."
                )
            return _view(record)

    def test(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        data: ConnectionInput,
    ) -> ConnectionTest:
        """Try ``data`` without saving it. A failure is a result (``ok = False`` with a
        safe message), not an HTTP error. With no password in ``data``, the stored one is
        used. A successful test of exactly the stored settings refreshes
        ``last_tested_at`` and ``can_write``."""
        self._workspaces.authorize(user, Action.MANAGE_CONNECTION, workspace_id)
        data = _clean(data)
        with Session(self._engine) as db, db.begin():
            self._load_system(db, workspace_id, system_id)
            record = self._load_connection(db, system_id, lock=True)
            password = data.password if data.password is not None else self._stored_password(record)
            result = self._run_test(data, password)
            if result.ok and record is not None and data.password is None and _same(record, data):
                record.last_tested_at = self._clock()
                record.can_write = result.can_write
        return result

    def save(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        data: ConnectionInput,
    ) -> tuple[Connection, bool]:
        """Create the Connection or replace its settings; returns it and whether it was
        created. Saving does not require a successful test (the database may be down for
        now); the UI tests first. Any change resets the last test result."""
        self._workspaces.authorize(user, Action.MANAGE_CONNECTION, workspace_id)
        data = _clean(data)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            system = self._load_system(db, workspace_id, system_id, lock=True)
            record = self._load_connection(db, system_id)
            created = record is None
            if record is None:
                record = ConnectionRecord(
                    id=uuid.uuid4(),
                    source_system_id=system_id,
                    secret_encrypted=None,
                    can_write=None,
                    last_tested_at=None,
                    created_by=user.id,
                    created_at=now,
                )
                db.add(record)
            changed_credentials = data.password is not None
            if data.password is not None:
                record.secret_encrypted = (
                    self._box.encrypt(data.password, context=PASSWORD_CONTEXT)
                    if data.password
                    else None
                )
            if not created and not _same(record, data):
                record.can_write = None
                record.last_tested_at = None
            record.engine = data.engine
            record.host = data.host
            record.port = data.port
            record.database = data.database
            record.username = data.username
            record.options = data.options
            record.allowed_schemas = data.allowed_schemas
            record.updated_at = now
            db.flush()
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="connection.created" if created else "connection.updated",
                object_type="connection",
                object_id=record.id,
                object_label=system.name,
                # No host, user or secret: the feed is visible to every member.
                details={"engine": record.engine, "credentials_changed": changed_credentials},
                at=now,
            )
            return _view(record), created

    # -- internals ------------------------------------------------------------------

    def _run_test(self, data: ConnectionInput, password: str | None) -> ConnectionTest:
        params = ConnectionParams(
            host=data.host,
            port=data.port,
            database=data.database,
            username=data.username,
            password=password,
            allowed_schemas=tuple(data.allowed_schemas),
            options=data.options,
        )
        try:
            return self._connectors(data.engine, params).test()
        except ConnectorError as exc:
            return ConnectionTest(ok=False, error_code=exc.code, error=exc.message)

    def _stored_password(self, record: ConnectionRecord | None) -> str | None:
        if record is None or record.secret_encrypted is None:
            return None
        try:
            return self._box.decrypt(record.secret_encrypted, context=PASSWORD_CONTEXT)
        except DecryptionError:
            raise ApiError(
                409,
                "connection_secret_unreadable",
                "The stored password does not decrypt with DAWAM_ENCRYPTION_KEY. "
                "Enter the password again.",
            ) from None

    def _load_system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID, *, lock: bool = False
    ) -> SourceSystemRecord:
        query = sa.select(SourceSystemRecord).where(
            SourceSystemRecord.id == system_id, SourceSystemRecord.status == "present"
        )
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        if record is None or record.workspace_id != workspace_id:
            raise _not_found()
        return record

    def _load_connection(
        self, db: Session, system_id: uuid.UUID, *, lock: bool = False
    ) -> ConnectionRecord | None:
        query = sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
        if lock:
            query = query.with_for_update()
        return db.scalars(query).first()


def _same(record: ConnectionRecord, data: ConnectionInput) -> bool:
    return (
        record.engine == data.engine
        and record.host == data.host
        and record.port == data.port
        and record.database == data.database
        and record.username == data.username
        and record.options == data.options
        and list(record.allowed_schemas) == data.allowed_schemas
        and data.password is None
    )
