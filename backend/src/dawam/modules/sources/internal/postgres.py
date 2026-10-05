"""The PostgreSQL Connector (spec story 40).

Every session is read-only (``default_transaction_read_only`` at connect time and
``read_only`` on the transaction), has a statement timeout (default 30 s) and a
``search_path`` of the allowed Database Schemas only. Errors are reduced to a fixed set
of safe messages: psycopg's own text can name the host, the user or a path.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql

from .connector import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    ColumnInfo,
    ColumnProfile,
    ConnectionParams,
    ConnectionTest,
    ConnectorError,
    QueryResult,
    RoutineInfo,
    ScopeError,
    SourceCatalog,
    TableInfo,
)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")

_SYSTEM_SCHEMA_CLAUSE = "nspname <> 'information_schema' AND nspname !~ '^pg_'"
_CATALOG_REFERENCE = re.compile(r"(?i)(?<![\w$])(pg_\w*|information_schema)(?![\w$])")


def _error_for(exc: psycopg.Error) -> ConnectorError:
    """A safe, fixed message for what went wrong (never the driver's text)."""
    state = exc.sqlstate or ""
    if not state:
        # libpq connection failures carry no SQLSTATE: classify by message (never shown).
        text = str(exc).lower()
        if "password authentication failed" in text or "no password supplied" in text:
            state = "28P01"
        elif "does not exist" in text and "database" in text:
            state = "3D000"
    if state in ("28P01", "28000"):
        return ConnectorError(
            "authentication_failed", "The database rejected the username or password."
        )
    if state == "3D000":
        return ConnectorError("database_not_found", "The database does not exist on that server.")
    if state == "57014":
        return ConnectorError(
            "timeout", "The database did not answer within the statement timeout."
        )
    if state == "25006":
        return ConnectorError("read_only", "DAWAM only reads from source databases.")
    if state == "42501":
        return ConnectorError("permission_denied", "The database user lacks a needed privilege.")
    if state.startswith("42"):
        return ConnectorError("invalid_query", "The database could not run that query.")
    if isinstance(exc, psycopg.OperationalError):
        return ConnectorError(
            "connection_failed",
            "Could not connect to the database server. Check the host, port and network access.",
        )
    return ConnectorError("database_error", "The database reported an error.")


def _option_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


class PostgresConnector:
    def __init__(self, params: ConnectionParams) -> None:
        self._params = params
        self._timeout_s = _option_int(
            params.options, "statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS
        )

    # -- sessions ---------------------------------------------------------------------

    @contextmanager
    def _session(self) -> Iterator[psycopg.Connection]:
        params = self._params
        ssl_mode = params.options.get("sslmode")
        extra: dict[str, Any] = {"sslmode": ssl_mode} if ssl_mode in SSL_MODES else {}
        try:
            connection = psycopg.connect(
                host=params.host,
                port=params.port,
                dbname=params.database,
                user=params.username,
                password=params.password or None,
                connect_timeout=_option_int(
                    params.options, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
                ),
                application_name="dawam",
                options="-c default_transaction_read_only=on",
                **extra,
            )
        except psycopg.Error as exc:
            raise _error_for(exc) from None
        try:
            connection.read_only = True
            with connection:
                try:
                    with connection.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('statement_timeout', %s, true),"
                            " set_config('search_path', %s, true)",
                            (str(self._timeout_s * 1000), self._search_path()),
                        )
                    yield connection
                except psycopg.Error as exc:
                    raise _error_for(exc) from None
        finally:
            connection.close()

    def _search_path(self) -> str:
        return ", ".join(
            '"' + name.replace('"', '""') + '"' for name in self._params.allowed_schemas
        )

    def _require_allowed(self, schema: str) -> None:
        if schema not in self._params.allowed_schemas:
            raise ScopeError()

    # -- the Connector interface --------------------------------------------------------

    def test(self) -> ConnectionTest:
        try:
            with self._session() as conn, conn.cursor() as cur:
                cur.execute("SHOW server_version")
                version = str(cur.fetchone()[0])  # type: ignore[index]
                cur.execute(
                    f"SELECT nspname FROM pg_namespace WHERE {_SYSTEM_SCHEMA_CLAUSE}"
                    " AND has_schema_privilege(nspname, 'USAGE') ORDER BY nspname"
                )
                available = tuple(row[0] for row in cur.fetchall())
                allowed = self._params.allowed_schemas
                missing = tuple(name for name in allowed if name not in available)
                cur.execute(
                    """
                    SELECT
                      COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false)
                      OR EXISTS (
                        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('r', 'p')
                          AND has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
                      )
                      OR EXISTS (
                        SELECT 1 FROM pg_namespace n
                        WHERE n.nspname = ANY(%(s)s) AND has_schema_privilege(n.oid, 'CREATE')
                      )
                    """,
                    {"s": list(allowed)},
                )
                can_write = bool(cur.fetchone()[0])  # type: ignore[index]
        except ConnectorError as exc:
            return ConnectionTest(ok=False, error_code=exc.code, error=exc.message)
        return ConnectionTest(
            ok=True,
            server_version=version,
            can_write=can_write,
            available_schemas=available,
            missing_schemas=missing,
        )

    def list_schemas(self) -> tuple[str, ...]:
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
                (list(self._params.allowed_schemas),),
            )
            existing = {row[0] for row in cur.fetchall()}
        return tuple(name for name in self._params.allowed_schemas if name in existing)

    def extract(self) -> SourceCatalog:
        allowed = list(self._params.allowed_schemas)
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname, c.relname, c.oid, c.relkind, c.reltuples::bigint,
                       obj_description(c.oid, 'pg_class'),
                       CASE WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid) END
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                ORDER BY n.nspname, c.relname
                """,
                (allowed,),
            )
            table_rows = cur.fetchall()
            cur.execute(
                """
                SELECT a.attrelid, a.attname, a.attnum, format_type(a.atttypid, a.atttypmod),
                       NOT a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
                       col_description(a.attrelid, a.attnum),
                       a.attnum = ANY(COALESCE(
                         (SELECT i.indkey::int2[] FROM pg_index i
                          WHERE i.indrelid = a.attrelid AND i.indisprimary), '{}'))
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attrelid, a.attnum
                """,
                (allowed,),
            )
            columns: dict[int, list[ColumnInfo]] = {}
            for oid, name, ordinal, data_type, nullable, default, comment, is_pk in cur.fetchall():
                columns.setdefault(oid, []).append(
                    ColumnInfo(name, ordinal, data_type, nullable, bool(is_pk), default, comment)
                )
            cur.execute(
                """
                SELECT n.nspname, p.proname, p.prokind, pg_get_functiondef(p.oid)
                FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = ANY(%s) AND p.prokind IN ('f', 'p')
                ORDER BY n.nspname, p.proname, p.oid
                """,
                (allowed,),
            )
            routines = tuple(
                RoutineInfo(schema, name, "procedure" if kind == "p" else "function", definition)
                for schema, name, kind, definition in cur.fetchall()
            )
        tables = tuple(
            TableInfo(
                schema=schema,
                name=name,
                kind="view" if relkind in ("v", "m") else "table",
                row_estimate=None if estimate is None or estimate < 0 else estimate,
                comment=comment,
                definition=definition,
                columns=tuple(columns.get(oid, [])),
            )
            for schema, name, oid, relkind, estimate, comment, definition in table_rows
        )
        return SourceCatalog(tables=tables, routines=routines)

    def profile(self, schema: str, table: str, column: str) -> ColumnProfile:
        self._require_allowed(schema)
        statement = sql.SQL(
            "SELECT count(*), count(*) - count({col}), count(DISTINCT {col}) FROM {schema}.{table}"
        ).format(
            col=sql.Identifier(column), schema=sql.Identifier(schema), table=sql.Identifier(table)
        )
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(statement)
            total, nulls, distinct = cur.fetchone()  # type: ignore[misc]
        return ColumnProfile(row_count=total, null_count=nulls, distinct_count=distinct)

    def sample(self, schema: str, table: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        self._require_allowed(schema)
        statement = sql.SQL("SELECT * FROM {schema}.{table}").format(
            schema=sql.Identifier(schema), table=sql.Identifier(table)
        )
        return self._run(statement, limit)

    def query(self, sql_text: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        """Run one statement. Defence in depth, not a SQL parser: the session is read-only
        with a search path of the allowed schemas, one statement only, and text naming a
        schema outside the allowed list (or a ``pg_*`` / ``information_schema`` catalog)
        is refused. Give the Connection's database user no more than read access.

        NOT SAFE TO EXPOSE: no route may reach this until the sqlglot statement guard and
        the function allow-list of spec §6.5 exist; ``dblink_exec`` or ``COPY ... TO
        PROGRAM`` get around these text checks."""
        text = sql_text.strip().rstrip(";").strip()
        if not text or ";" in text:
            raise ConnectorError("invalid_query", "Send exactly one SQL statement.")
        if _CATALOG_REFERENCE.search(text):
            raise ScopeError()
        for name in self._schemas_outside_scope():
            if re.search(rf'(?i)(?<![\w$])"?{re.escape(name)}"?\s*\.', text):
                raise ScopeError()
        return self._run(sql.SQL(text), limit)  # type: ignore[arg-type]

    def _schemas_outside_scope(self) -> list[str]:
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname <> ALL(%s)",
                (list(self._params.allowed_schemas),),
            )
            return [row[0] for row in cur.fetchall()]

    def _run(self, statement: sql.Composable, limit: int) -> QueryResult:
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(statement)  # type: ignore[call-overload]
            names = tuple(d.name for d in cur.description or ())
            rows = cur.fetchmany(limit + 1)
        return QueryResult(
            columns=names, rows=tuple(tuple(r) for r in rows[:limit]), truncated=len(rows) > limit
        )
