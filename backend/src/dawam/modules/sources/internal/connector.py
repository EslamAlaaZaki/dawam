"""The Connector interface: how DAWAM talks to a source database (spec §8, story 40).

One implementation per engine (PostgreSQL today; MySQL/MariaDB, SQL Server and Oracle
later). Every method opens its own short-lived session, so a Connector holds no state
beyond the ``ConnectionParams`` it was built from.

Rules every implementation keeps:

- nothing outside ``ConnectionParams.allowed_schemas`` is ever queried;
- every statement runs under the statement timeout and, where the engine supports it,
  in a read-only transaction;
- an error that leaves a Connector is a ``ConnectorError`` whose message is safe to
  show a user and to log: it never contains the host, user, password or driver text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30
DEFAULT_ROW_LIMIT = 100


@dataclass(frozen=True)
class ConnectionParams:
    """Everything a Connector needs. The password is in clear, only ever in memory, and
    neither it nor the host appears in ``repr``."""

    host: str = field(repr=False)
    port: int = field(repr=False)
    database: str = field(repr=False)
    username: str = field(repr=False)
    password: str | None = field(repr=False)
    allowed_schemas: tuple[str, ...]
    options: dict[str, Any] = field(default_factory=dict)


class ConnectorError(Exception):
    """A failure that is safe to show: ``code`` is stable, ``message`` leaks nothing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScopeError(ConnectorError):
    """A request for something outside the allowed Database Schemas."""

    def __init__(self, message: str = "That is outside the allowed Database Schemas.") -> None:
        super().__init__("schema_not_allowed", message)


@dataclass(frozen=True)
class ConnectionTest:
    ok: bool
    error_code: str | None = None
    error: str | None = None
    server_version: str | None = None
    can_write: bool | None = None
    """``True`` if the user could change data or objects in the allowed schemas."""
    available_schemas: tuple[str, ...] = ()
    """Every non-system Database Schema the user can see (to pick the allowed ones from)."""
    missing_schemas: tuple[str, ...] = ()
    """Allowed schemas that do not exist (or that the user cannot see)."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    ordinal: int
    data_type: str
    is_nullable: bool
    is_pk: bool
    default: str | None
    comment: str | None


@dataclass(frozen=True)
class ConstraintInfo:
    name: str
    type: str
    """``pk``, ``fk`` or ``unique``."""
    columns: tuple[str, ...]
    ref_schema: str | None = None
    """A foreign key's referenced Database Schema, table and columns."""
    ref_table: str | None = None
    ref_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndexInfo:
    name: str
    columns: tuple[str, ...]
    """Column names; an expression key is its SQL text (e.g. ``lower(email)``)."""
    is_unique: bool


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    kind: str
    """``table`` or ``view``."""
    row_estimate: int | None
    comment: str | None
    definition: str | None
    """A view's SQL."""
    columns: tuple[ColumnInfo, ...]
    constraints: tuple[ConstraintInfo, ...] = ()
    indexes: tuple[IndexInfo, ...] = ()


@dataclass(frozen=True)
class RoutineInfo:
    schema: str
    name: str
    kind: str
    """``procedure`` or ``function``."""
    definition: str | None
    signature: str = ""
    """The argument list that tells overloads apart (``""`` where the engine has none)."""


@dataclass(frozen=True)
class SourceCatalog:
    tables: tuple[TableInfo, ...]
    routines: tuple[RoutineInfo, ...]


@dataclass(frozen=True)
class ColumnProfile:
    row_count: int
    null_count: int
    distinct_count: int


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


class Connector(Protocol):
    def test(self) -> ConnectionTest:
        """Connect and report success, or a clear error (never raises ``ConnectorError``)."""
        ...

    def list_schemas(self) -> tuple[str, ...]:
        """The allowed Database Schemas that exist."""
        ...

    def extract(self) -> SourceCatalog:
        """Tables, views, columns, constraints, indexes and routines of the allowed
        schemas."""
        ...

    def profile(self, schema: str, table: str, column: str) -> ColumnProfile:
        """Row, null and distinct counts of one column."""
        ...

    def sample(self, schema: str, table: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        """A few rows of one table."""
        ...

    def query(self, sql: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        """A read-only query over the allowed schemas, capped at ``limit`` rows.

        NOT SAFE TO EXPOSE: no route may reach this (directly or through a service) until
        the sqlglot statement guard and the function allow-list of spec §6.5 exist. The
        current scoping is a best-effort text check, which e.g. ``dblink_exec`` or
        ``COPY ... TO PROGRAM`` get around."""
        ...


class ConnectorFactory(Protocol):
    def __call__(self, engine: str, params: ConnectionParams) -> Connector: ...


def connector_for(engine: str, params: ConnectionParams) -> Connector:
    """The registry: the Connector for ``engine`` (``ConnectorError`` if unsupported)."""
    from .postgres import PostgresConnector

    if engine == "postgresql":
        return PostgresConnector(params)
    raise ConnectorError("unsupported_engine", f"The engine {engine!r} is not supported yet.")
