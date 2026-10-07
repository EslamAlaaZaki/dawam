"""The MySQL / MariaDB Connector (spec story 40).

Every session runs in a read-only transaction (``START TRANSACTION READ ONLY``), has a
statement timeout (default 30 s: ``max_execution_time`` on MySQL, ``max_statement_time`` on
MariaDB, plus a client-side read timeout as a backstop) and a default Database Schema
inside the allowed list. In MySQL a "schema" is a database. Errors are reduced to a fixed
set of safe messages: the driver's own text can name the host, the user or a path.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import Any

import pymysql
from pymysql.connections import Connection as MySqlConnection

from . import profiling
from .connector import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    ColumnInfo,
    ColumnProfile,
    ColumnStats,
    ConnectionParams,
    ConnectionTest,
    ConnectorError,
    ConstraintInfo,
    IndexInfo,
    QueryResult,
    RoutineInfo,
    ScopeError,
    SourceCatalog,
    TableInfo,
)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

_SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")
_CONSTRAINT_TYPES = {"PRIMARY KEY": "pk", "FOREIGN KEY": "fk", "UNIQUE": "unique"}
_CATALOG_REFERENCE = re.compile(r"(?i)(?<![\w$])`?(" + "|".join(_SYSTEM_SCHEMAS) + r")`?\s*\.")
_READ_STATEMENTS = ("select", "with", "show", "explain", "describe", "desc", "table", "values")
# Privileges that let the user change data or objects (``USAGE`` and ``SELECT`` do not).
_WRITE_PRIVILEGES = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "DROP",
    "ALTER",
    "INDEX",
    "CREATE VIEW",
    "CREATE ROUTINE",
    "ALTER ROUTINE",
    "TRIGGER",
    "REFERENCES",
    "SUPER",
    "FILE",
    "ALL PRIVILEGES",
}

# MySQL: 1045/1044 access denied, 1049 unknown database, 1142/1143/1227/1370 privilege,
# 1792/1290 read-only, 3024 (MySQL) / 1969 (MariaDB) statement timeout, 2002/2003/2005/2013
# connection problems.
_AUTH = {1045, 1698}
_DENIED = {1044, 1142, 1143, 1227, 1370, 1095}
_TIMEOUT = {3024, 1969, 1317}
_READ_ONLY = {1792, 1290, 1207}


def _error_for(exc: pymysql.err.MySQLError) -> ConnectorError:
    """A safe, fixed message for what went wrong (never the driver's text)."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else 0
    if code in _AUTH:
        return ConnectorError(
            "authentication_failed", "The database rejected the username or password."
        )
    if code == 1049:
        return ConnectorError("database_not_found", "The database does not exist on that server.")
    if code in _TIMEOUT or (code == 2013 and "timed out" in str(exc).lower()):
        return ConnectorError(
            "timeout", "The database did not answer within the statement timeout."
        )
    if code in _READ_ONLY:
        return ConnectorError("read_only", "DAWAM only reads from source databases.")
    if code in _DENIED:
        return ConnectorError("permission_denied", "The database user lacks a needed privilege.")
    if isinstance(exc, pymysql.err.OperationalError) and code in (2002, 2003, 2005, 2006, 2013):
        return ConnectorError(
            "connection_failed",
            "Could not connect to the database server. Check the host, port and network access.",
        )
    if isinstance(exc, pymysql.err.ProgrammingError):
        return ConnectorError("invalid_query", "The database could not run that query.")
    if isinstance(exc, pymysql.err.OperationalError):
        return ConnectorError(
            "connection_failed",
            "Could not connect to the database server. Check the host, port and network access.",
        )
    return ConnectorError("database_error", "The database reported an error.")


def _option_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _marks(values: Sequence[object]) -> str:
    return ", ".join(["%s"] * len(values))


class MySqlConnector:
    def __init__(self, params: ConnectionParams) -> None:
        self._params = params
        self._timeout_s = _option_int(
            params.options, "statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS
        )
        self._mariadb = False

    # -- sessions ---------------------------------------------------------------------

    def _tls(self) -> ssl.SSLContext | None:
        mode = self._params.options.get("sslmode")
        if mode not in ("require", "verify-ca", "verify-full"):
            return None
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        if mode == "require":
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif mode == "verify-ca":
            context.check_hostname = False
        return context

    @contextmanager
    def _session(self) -> Iterator[MySqlConnection]:
        params = self._params
        try:
            connection = pymysql.connect(
                host=params.host,
                port=params.port,
                user=params.username,
                password=params.password or "",
                connect_timeout=_option_int(
                    params.options, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
                ),
                # The server's own timeout fires first; this only stops a hung socket.
                read_timeout=self._timeout_s + 5,
                charset="utf8mb4",
                autocommit=True,
                ssl=self._tls(),
                program_name="dawam",
            )
        except pymysql.err.MySQLError as exc:
            raise _error_for(exc) from None
        try:
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT VERSION()")
                    self._mariadb = "mariadb" in str(cur.fetchone()[0]).lower()  # type: ignore[index]
                    if self._mariadb:
                        cur.execute(f"SET SESSION max_statement_time = {int(self._timeout_s)}")
                    else:
                        cur.execute(
                            f"SET SESSION max_execution_time = {int(self._timeout_s) * 1000}"
                        )
                    default = self._default_schema()
                    if default is not None:
                        cur.execute(f"USE {_quote(default)}")
                    cur.execute("START TRANSACTION READ ONLY")
                yield connection
            except pymysql.err.MySQLError as exc:
                raise _error_for(exc) from None
        finally:
            with suppress(pymysql.err.MySQLError):
                connection.rollback()
            connection.close()

    def _default_schema(self) -> str | None:
        allowed = self._params.allowed_schemas
        if self._params.database in allowed:
            return self._params.database
        return allowed[0] if allowed else None

    def _require_allowed(self, schema: str) -> None:
        if schema not in self._params.allowed_schemas:
            raise ScopeError()

    # -- the Connector interface --------------------------------------------------------

    def test(self) -> ConnectionTest:
        try:
            with self._session() as conn, conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                version = str(cur.fetchone()[0])  # type: ignore[index]
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata"
                    f" WHERE schema_name NOT IN ({_marks(_SYSTEM_SCHEMAS)}) ORDER BY schema_name",
                    _SYSTEM_SCHEMAS,
                )
                available = tuple(row[0] for row in cur.fetchall())
                allowed = self._params.allowed_schemas
                missing = tuple(name for name in allowed if name not in available)
                can_write = self._can_write(cur, allowed)
        except ConnectorError as exc:
            return ConnectionTest(ok=False, error_code=exc.code, error=exc.message)
        return ConnectionTest(
            ok=True,
            server_version=version,
            can_write=can_write,
            available_schemas=available,
            missing_schemas=missing,
        )

    def _can_write(self, cur: Any, allowed: Sequence[str]) -> bool:
        cur.execute(
            "SELECT CONCAT(QUOTE(SUBSTRING_INDEX(CURRENT_USER(), '@', 1)), '@',"
            " QUOTE(SUBSTRING_INDEX(CURRENT_USER(), '@', -1)))"
        )
        grantee = cur.fetchone()[0]
        cur.execute(
            "SELECT privilege_type FROM information_schema.user_privileges WHERE grantee = %s",
            (grantee,),
        )
        if any(row[0] in _WRITE_PRIVILEGES for row in cur.fetchall()):
            return True
        for table in ("schema_privileges", "table_privileges"):
            cur.execute(
                f"SELECT table_schema, privilege_type FROM information_schema.{table}"
                " WHERE grantee = %s",
                (grantee,),
            )
            for schema, privilege in cur.fetchall():
                # Schema-level grants may use LIKE patterns with escaped underscores.
                name = str(schema).replace("\\_", "_").replace("\\%", "%")
                if privilege in _WRITE_PRIVILEGES and any(
                    name == s or (("%" in name) and re.fullmatch(_like(name), s) is not None)
                    for s in allowed
                ):
                    return True
        return False

    def list_schemas(self) -> tuple[str, ...]:
        allowed = self._params.allowed_schemas
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata"
                f" WHERE schema_name IN ({_marks(allowed)})",
                tuple(allowed),
            )
            existing = {row[0] for row in cur.fetchall()}
        return tuple(name for name in allowed if name in existing)

    def extract(self) -> SourceCatalog:
        allowed = tuple(self._params.allowed_schemas)
        marks = _marks(allowed)
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT schema_name FROM information_schema.schemata"
                f" WHERE schema_name IN ({marks}) ORDER BY schema_name",
                allowed,
            )
            schemas = tuple(row[0] for row in cur.fetchall())
            cur.execute(
                f"""
                SELECT t.table_schema, t.table_name, t.table_type, t.table_rows, t.table_comment,
                       v.view_definition
                FROM information_schema.tables t
                LEFT JOIN information_schema.views v
                  ON v.table_schema = t.table_schema AND v.table_name = t.table_name
                WHERE t.table_schema IN ({marks})
                  AND t.table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY t.table_schema, t.table_name
                """,
                allowed,
            )
            table_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT table_schema, table_name, column_name, ordinal_position, column_type,
                       is_nullable, column_default, column_comment, column_key
                FROM information_schema.columns
                WHERE table_schema IN ({marks})
                ORDER BY table_schema, table_name, ordinal_position
                """,
                allowed,
            )
            columns: dict[tuple[str, str], list[ColumnInfo]] = {}
            for (
                schema,
                table,
                name,
                ordinal,
                data_type,
                nullable,
                default,
                comment,
                key,
            ) in cur.fetchall():
                is_nullable = nullable == "YES"
                if self._mariadb and default == "NULL" and is_nullable:
                    default = None  # MariaDB spells "no default" as the text NULL
                columns.setdefault((schema, table), []).append(
                    ColumnInfo(
                        name,
                        ordinal,
                        data_type,
                        is_nullable,
                        key == "PRI",
                        default,
                        comment or None,
                    )
                )
            cur.execute(
                f"""
                SELECT tc.table_schema, tc.table_name, tc.constraint_name, tc.constraint_type,
                       k.column_name, k.referenced_table_schema, k.referenced_table_name,
                       k.referenced_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage k
                  ON k.constraint_schema = tc.constraint_schema
                 AND k.constraint_name = tc.constraint_name
                 AND k.table_schema = tc.table_schema
                 AND k.table_name = tc.table_name
                WHERE tc.table_schema IN ({marks})
                  AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
                ORDER BY tc.table_schema, tc.table_name, tc.constraint_name, k.ordinal_position
                """,
                allowed,
            )
            grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
            for (
                schema,
                table,
                name,
                kind,
                column,
                ref_schema,
                ref_table,
                ref_column,
            ) in cur.fetchall():
                entry = grouped.setdefault(
                    (schema, table, name),
                    {
                        "type": _CONSTRAINT_TYPES[kind],
                        "columns": [],
                        "ref_schema": ref_schema,
                        "ref_table": ref_table,
                        "ref_columns": [],
                    },
                )
                entry["columns"].append(column)
                if ref_column is not None:
                    entry["ref_columns"].append(ref_column)
            constraints: dict[tuple[str, str], list[ConstraintInfo]] = {}
            for (schema, table, name), entry in grouped.items():
                constraints.setdefault((schema, table), []).append(
                    ConstraintInfo(
                        name=name,
                        type=entry["type"],
                        columns=tuple(entry["columns"]),
                        ref_schema=entry["ref_schema"],
                        ref_table=entry["ref_table"],
                        ref_columns=tuple(entry["ref_columns"]),
                    )
                )
            expression = "NULL" if self._mariadb else "expression"
            cur.execute(
                f"""
                SELECT table_schema, table_name, index_name, non_unique, column_name,
                       {expression}
                FROM information_schema.statistics
                WHERE table_schema IN ({marks})
                ORDER BY table_schema, table_name, index_name, seq_in_index
                """,
                allowed,
            )
            index_parts: dict[tuple[str, str, str], dict[str, Any]] = {}
            for schema, table, name, non_unique, column, expr in cur.fetchall():
                entry = index_parts.setdefault(
                    (schema, table, name), {"unique": not non_unique, "columns": []}
                )
                # A functional key part has no column: its SQL, without MySQL's backticks.
                entry["columns"].append(
                    column if column is not None else str(expr).replace("`", "")
                )
            indexes: dict[tuple[str, str], list[IndexInfo]] = {}
            for (schema, table, name), entry in index_parts.items():
                indexes.setdefault((schema, table), []).append(
                    IndexInfo(name, tuple(entry["columns"]), entry["unique"])
                )
            cur.execute(
                f"""
                SELECT specific_schema, specific_name, ordinal_position, parameter_mode,
                       parameter_name, dtd_identifier
                FROM information_schema.parameters
                WHERE specific_schema IN ({marks}) AND ordinal_position > 0
                ORDER BY specific_schema, specific_name, ordinal_position
                """,
                allowed,
            )
            parameters: dict[tuple[str, str], list[str]] = {}
            for schema, name, _, mode, parameter, data_type in cur.fetchall():
                prefix = f"{mode} " if mode in ("OUT", "INOUT") else ""
                parameters.setdefault((schema, name), []).append(f"{prefix}{parameter} {data_type}")
            cur.execute(
                f"""
                SELECT routine_schema, routine_name, routine_type, routine_definition
                FROM information_schema.routines
                WHERE routine_schema IN ({marks}) AND routine_type IN ('FUNCTION', 'PROCEDURE')
                ORDER BY routine_schema, routine_name
                """,
                allowed,
            )
            routines = tuple(
                RoutineInfo(
                    schema,
                    name,
                    kind.lower(),
                    definition,
                    ", ".join(parameters.get((schema, name), [])),
                )
                for schema, name, kind, definition in cur.fetchall()
            )
        tables = tuple(
            TableInfo(
                schema=schema,
                name=name,
                kind="view" if kind == "VIEW" else "table",
                row_estimate=None if kind == "VIEW" or estimate is None else int(estimate),
                comment=None if kind == "VIEW" or not comment else comment,
                definition=(definition or None) if kind == "VIEW" else None,
                columns=tuple(columns.get((schema, name), [])),
                constraints=tuple(constraints.get((schema, name), [])),
                indexes=tuple(indexes.get((schema, name), [])),
            )
            for schema, name, kind, estimate, comment, definition in table_rows
        )
        return SourceCatalog(tables=tables, routines=routines, schemas=schemas)

    def profile(self, schema: str, table: str, column: str) -> ColumnProfile:
        self._require_allowed(schema)
        col = _quote(column)
        statement = (
            f"SELECT count(*), count(*) - count({col}), count(DISTINCT {col})"
            f" FROM {_quote(schema)}.{_quote(table)}"
        )
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(statement)
            total, nulls, distinct = cur.fetchone()  # type: ignore[misc]
        return ColumnProfile(row_count=int(total), null_count=int(nulls), distinct_count=distinct)

    def profile_column(
        self,
        schema: str,
        table: str,
        column: str,
        data_type: str,
        *,
        row_cap: int,
        top_n: bool,
        with_min_max: bool,
    ) -> ColumnStats:
        self._require_allowed(schema)
        return profiling.profile_column(
            self._run,
            "mysql",
            schema,
            table,
            column,
            data_type,
            row_cap=row_cap,
            top_n=top_n,
            with_min_max=with_min_max,
        )

    def sample(self, schema: str, table: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        self._require_allowed(schema)
        return self._run(f"SELECT * FROM {_quote(schema)}.{_quote(table)}", limit)

    def query(self, sql_text: str, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
        """Run one statement. Defence in depth, not a SQL parser: the session is read-only
        with a default schema inside the allowed list, one statement only, a leading
        keyword that reads (MySQL commits implicitly on DDL, which would end the read-only
        transaction), and text naming a schema outside the allowed list (or a system
        schema) is refused. Give the Connection's database user no more than read access.

        NOT SAFE TO EXPOSE: no route may reach this until the sqlglot statement guard and
        the function allow-list of spec §6.5 exist."""
        text = sql_text.strip().rstrip(";").strip()
        if not text or ";" in text:
            raise ConnectorError("invalid_query", "Send exactly one SQL statement.")
        if re.split(r"\W+", text, maxsplit=1)[0].lower() not in _READ_STATEMENTS:
            raise ConnectorError("read_only", "DAWAM only reads from source databases.")
        if _CATALOG_REFERENCE.search(text):
            raise ScopeError()
        for name in self._schemas_outside_scope():
            if re.search(rf"(?i)(?<![\w$])`?{re.escape(name)}`?\s*\.", text):
                raise ScopeError()
        return self._run(text, limit)

    def _schemas_outside_scope(self) -> list[str]:
        allowed = tuple(self._params.allowed_schemas)
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata"
                f" WHERE schema_name NOT IN ({_marks(allowed)})",
                allowed,
            )
            return [row[0] for row in cur.fetchall()]

    def _run(self, statement: str, limit: int) -> QueryResult:
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(statement)
            names = tuple(d[0] for d in cur.description or ())
            rows = cur.fetchmany(limit + 1)
        return QueryResult(
            columns=names, rows=tuple(tuple(r) for r in rows[:limit]), truncated=len(rows) > limit
        )


def _like(pattern: str) -> str:
    """A SQL LIKE pattern (``%`` only) as a regular expression."""
    return ".*".join(re.escape(part) for part in pattern.split("%"))
