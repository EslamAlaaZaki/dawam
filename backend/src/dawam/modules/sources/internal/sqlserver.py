"""The SQL Server Connector (spec story 40).

SQL Server has no read-only transaction, so read-only is enforced in layers: ``query``
runs only a single ``SELECT`` / ``WITH`` statement without ``INTO``, every other statement
is one this module builds, and ``test`` warns when the login could write. Give the
Connection's login no more than read access. Every session
has a query timeout (default 30 s) and every name is schema-qualified: nothing outside the
allowed Database Schemas is queried. Errors are reduced to a fixed set of safe messages.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import pymssql

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

_SYSTEM_SCHEMAS = ("sys", "INFORMATION_SCHEMA", "guest")
_FIXED_ROLE_SCHEMA_ID = 16384
_CATALOG_REFERENCE = re.compile(
    r"(?i)(?<![\w$@#])(sys\w*|information_schema|master|msdb|tempdb|model"
    r"|openrowset|openquery|opendatasource|xp_\w*|sp_\w*)(?![\w$])"
)
_STARTS_READ_ONLY = re.compile(r"(?is)^\s*(select|with)\b")
_INTO = re.compile(r"(?i)(?<![\w$@#])into(?![\w$])")
_CONSTRAINT_TYPES = {"PK": "pk", "UQ": "unique"}
_LENGTH_TYPES = {"char", "varchar", "binary", "varbinary"}
_NATIONAL_TYPES = {"nchar", "nvarchar"}
_SCALE_TYPES = {"datetime2", "datetimeoffset", "time"}


def _error_for(exc: pymssql.Error) -> ConnectorError:
    """A safe, fixed message for what went wrong (never the driver's text)."""
    # pymssql raises ``(code, b"message")``, sometimes wrapped in a one-element tuple.
    args = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], tuple) else exc.args
    code = args[0] if args and isinstance(args[0], int) else 0
    text = " ".join(a.decode("utf-8", "replace") if isinstance(a, bytes) else str(a) for a in args)
    text = text.lower()
    # SQL Server reports a login that cannot reach its database as a failed login (18456),
    # so a missing database looks like a wrong password; 4060 is the rare explicit case.
    if code == 4060 or "cannot open database" in text:
        return ConnectorError("database_not_found", "The database does not exist on that server.")
    if code == 18456 or "login failed" in text:
        return ConnectorError(
            "authentication_failed", "The database rejected the username or password."
        )
    if code == 20003 or "timed out" in text:
        return ConnectorError(
            "timeout", "The database did not answer within the statement timeout."
        )
    if code in (229, 230, 262, 297, 300):
        return ConnectorError("permission_denied", "The database user lacks a needed privilege.")
    if isinstance(exc, pymssql.ProgrammingError):
        return ConnectorError("invalid_query", "The database could not run that query.")
    if isinstance(exc, pymssql.OperationalError):
        return ConnectorError(
            "connection_failed",
            "Could not connect to the database server. Check the host, port and network access.",
        )
    return ConnectorError("database_error", "The database reported an error.")


def _option_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _data_type(name: str, max_length: int, precision: int, scale: int) -> str:
    if name in _LENGTH_TYPES:
        return f"{name}({'max' if max_length == -1 else max_length})"
    if name in _NATIONAL_TYPES:
        return f"{name}({'max' if max_length == -1 else max_length // 2})"
    if name in ("decimal", "numeric"):
        return f"{name}({precision},{scale})"
    if name in _SCALE_TYPES:
        return f"{name}({scale})"
    return name


def _strip_parentheses(definition: str) -> str:
    """``((0))`` -> ``0``: SQL Server wraps stored defaults in redundant parentheses."""
    text = definition.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for position, char in enumerate(text):
            depth += (char == "(") - (char == ")")
            if depth == 0 and position < len(text) - 1:
                return text
        text = text[1:-1].strip()
    return text


class SqlServerConnector:
    def __init__(self, params: ConnectionParams) -> None:
        self._params = params
        self._timeout_s = _option_int(
            params.options, "statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS
        )

    # -- sessions ---------------------------------------------------------------------

    @contextmanager
    def _session(self) -> Iterator[Any]:
        params = self._params
        try:
            connection = pymssql.connect(
                server=params.host,
                port=str(params.port),
                database=params.database,
                user=params.username,
                password=params.password or "",
                login_timeout=_option_int(
                    params.options, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS
                ),
                timeout=self._timeout_s,
                appname="dawam",
                charset="UTF-8",
                autocommit=True,
            )
        except pymssql.Error as exc:
            raise _error_for(exc) from None
        try:
            try:
                yield connection
            except pymssql.Error as exc:
                raise _error_for(exc) from None
        finally:
            with suppress(pymssql.Error):
                connection.close()

    def _in_scope(self) -> tuple[str, ...]:
        return tuple(self._params.allowed_schemas)

    def _require_allowed(self, schema: str) -> None:
        if schema not in self._params.allowed_schemas:
            raise ScopeError()

    # -- the Connector interface --------------------------------------------------------

    def test(self) -> ConnectionTest:
        try:
            with self._session() as conn:
                cur = conn.cursor()
                cur.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128))")
                version = str(cur.fetchone()[0])
                cur.execute(
                    "SELECT name FROM sys.schemas WHERE name NOT IN %s AND schema_id < %s"
                    " ORDER BY name",
                    (_SYSTEM_SCHEMAS, _FIXED_ROLE_SCHEMA_ID),
                )
                available = tuple(row[0] for row in cur.fetchall())
                folded = {name.casefold() for name in available}
                allowed = self._in_scope()
                missing = tuple(name for name in allowed if name.casefold() not in folded)
                cur.execute(
                    """
                    SELECT CASE WHEN EXISTS (
                      SELECT 1 FROM sys.schemas s
                      WHERE s.name IN %(s)s AND (
                        HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', 'INSERT') = 1
                        OR HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', 'UPDATE') = 1
                        OR HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', 'DELETE') = 1
                        OR HAS_PERMS_BY_NAME(QUOTENAME(s.name), 'SCHEMA', 'ALTER') = 1)
                    ) OR EXISTS (
                      SELECT 1 FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
                      WHERE s.name IN %(s)s AND (
                        HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name),
                                          'OBJECT', 'INSERT') = 1
                        OR HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name),
                                             'OBJECT', 'UPDATE') = 1
                        OR HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(t.name),
                                             'OBJECT', 'DELETE') = 1)
                    ) THEN 1 ELSE 0 END
                    """,
                    {"s": allowed},
                )
                can_write = bool(cur.fetchone()[0])
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
        with self._session() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sys.schemas WHERE name IN %s", (self._in_scope(),))
            existing = {row[0].casefold() for row in cur.fetchall()}
        return tuple(name for name in self._params.allowed_schemas if name.casefold() in existing)

    def extract(self) -> SourceCatalog:
        allowed = self._in_scope()
        with self._session() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sys.schemas WHERE name IN %s ORDER BY name", (allowed,))
            existing = {row[0].casefold() for row in cur.fetchall()}
            schemas = tuple(sorted(name for name in allowed if name.casefold() in existing))
            cur.execute(
                """
                SELECT s.name, o.name, o.object_id, o.type,
                       (SELECT SUM(p.rows) FROM sys.partitions p
                        WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)),
                       CAST(ep.value AS nvarchar(max)),
                       CASE WHEN o.type = 'V' THEN OBJECT_DEFINITION(o.object_id) END
                FROM sys.objects o
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                LEFT JOIN sys.extended_properties ep
                  ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0
                 AND ep.name = 'MS_Description'
                WHERE s.name IN %s AND o.type IN ('U', 'V')
                ORDER BY s.name, o.name
                """,
                (allowed,),
            )
            table_rows = cur.fetchall()
            cur.execute(
                """
                SELECT c.object_id, c.name, c.column_id, t.name, c.max_length, c.precision,
                       c.scale, c.is_nullable, dc.definition,
                       CAST(ep.value AS nvarchar(max)),
                       CASE WHEN EXISTS (
                         SELECT 1 FROM sys.indexes i
                         JOIN sys.index_columns ic
                           ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                         WHERE i.object_id = c.object_id AND i.is_primary_key = 1
                           AND ic.column_id = c.column_id) THEN 1 ELSE 0 END
                FROM sys.columns c
                JOIN sys.objects o ON o.object_id = c.object_id
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                JOIN sys.types t ON t.user_type_id = c.user_type_id
                LEFT JOIN sys.default_constraints dc
                  ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
                LEFT JOIN sys.extended_properties ep
                  ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id
                 AND ep.name = 'MS_Description'
                WHERE s.name IN %s AND o.type IN ('U', 'V')
                ORDER BY c.object_id, c.column_id
                """,
                (allowed,),
            )
            columns: dict[int, list[ColumnInfo]] = {}
            for row in cur.fetchall():
                oid, name, ordinal, type_name, length, precision, scale = row[:7]
                nullable, default, comment, is_pk = row[7:]
                columns.setdefault(oid, []).append(
                    ColumnInfo(
                        name,
                        ordinal,
                        _data_type(type_name, length, precision, scale),
                        bool(nullable),
                        bool(is_pk),
                        _strip_parentheses(default) if default else None,
                        comment,
                    )
                )
            cur.execute(
                """
                SELECT kc.parent_object_id, kc.name, kc.type, c.name
                FROM sys.key_constraints kc
                JOIN sys.objects o ON o.object_id = kc.parent_object_id
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                JOIN sys.index_columns ic
                  ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
                 AND ic.is_included_column = 0
                JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                WHERE s.name IN %s AND kc.type IN ('PK', 'UQ')
                ORDER BY kc.parent_object_id, kc.name, ic.key_ordinal
                """,
                (allowed,),
            )
            keys: dict[tuple[int, str, str], list[str]] = {}
            for oid, name, kind, column in cur.fetchall():
                keys.setdefault((oid, name, kind), []).append(column)
            cur.execute(
                """
                SELECT fk.parent_object_id, fk.name, pc.name, rs.name, ro.name, rc.name
                FROM sys.foreign_keys fk
                JOIN sys.objects o ON o.object_id = fk.parent_object_id
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
                JOIN sys.columns pc
                  ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
                JOIN sys.objects ro ON ro.object_id = fk.referenced_object_id
                JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
                JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                 AND rc.column_id = fkc.referenced_column_id
                WHERE s.name IN %s
                ORDER BY fk.parent_object_id, fk.name, fkc.constraint_column_id
                """,
                (allowed,),
            )
            foreign: dict[tuple[int, str, str, str], tuple[list[str], list[str]]] = {}
            for oid, name, column, ref_schema, ref_table, ref_column in cur.fetchall():
                local, remote = foreign.setdefault((oid, name, ref_schema, ref_table), ([], []))
                local.append(column)
                remote.append(ref_column)
            constraints: dict[int, list[ConstraintInfo]] = {}
            for (oid, name, kind), cols in keys.items():
                constraints.setdefault(oid, []).append(
                    ConstraintInfo(name=name, type=_CONSTRAINT_TYPES[kind], columns=tuple(cols))
                )
            for (oid, name, ref_schema, ref_table), (cols, ref_cols) in foreign.items():
                constraints.setdefault(oid, []).append(
                    ConstraintInfo(
                        name=name,
                        type="fk",
                        columns=tuple(cols),
                        ref_schema=ref_schema,
                        ref_table=ref_table,
                        ref_columns=tuple(ref_cols),
                    )
                )
            for found in constraints.values():
                found.sort(key=lambda constraint: constraint.name)
            cur.execute(
                """
                SELECT i.object_id, i.name, i.is_unique, c.name
                FROM sys.indexes i
                JOIN sys.objects o ON o.object_id = i.object_id
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                JOIN sys.index_columns ic
                  ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                 AND ic.is_included_column = 0 AND ic.key_ordinal > 0
                JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                WHERE s.name IN %s AND o.type IN ('U', 'V') AND i.index_id > 0
                  AND i.name IS NOT NULL
                ORDER BY i.object_id, i.name, ic.key_ordinal
                """,
                (allowed,),
            )
            index_keys: dict[tuple[int, str, bool], list[str]] = {}
            for oid, name, is_unique, column in cur.fetchall():
                index_keys.setdefault((oid, name, bool(is_unique)), []).append(column)
            indexes: dict[int, list[IndexInfo]] = {}
            for (oid, name, is_unique), cols in index_keys.items():
                indexes.setdefault(oid, []).append(IndexInfo(name, tuple(cols), is_unique))
            cur.execute(
                """
                SELECT o.object_id, s.name, o.name, o.type, m.definition
                FROM sys.objects o
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
                WHERE s.name IN %s AND o.type IN ('P', 'FN', 'IF', 'TF')
                ORDER BY s.name, o.name
                """,
                (allowed,),
            )
            routine_rows = cur.fetchall()
            cur.execute(
                """
                SELECT p.object_id, p.name, t.name, p.max_length, p.precision, p.scale
                FROM sys.parameters p
                JOIN sys.objects o ON o.object_id = p.object_id
                JOIN sys.schemas s ON s.schema_id = o.schema_id
                JOIN sys.types t ON t.user_type_id = p.user_type_id
                WHERE s.name IN %s AND p.parameter_id > 0
                ORDER BY p.object_id, p.parameter_id
                """,
                (allowed,),
            )
            signatures: dict[int, list[str]] = {}
            for oid, name, type_name, length, precision, scale in cur.fetchall():
                signatures.setdefault(oid, []).append(
                    f"{name} {_data_type(type_name, length, precision, scale)}"
                )
        routines = tuple(
            RoutineInfo(
                schema,
                name,
                "procedure" if kind.strip() == "P" else "function",
                definition,
                ", ".join(signatures.get(oid, [])),
            )
            for oid, schema, name, kind, definition in routine_rows
        )
        tables = tuple(
            TableInfo(
                schema=schema,
                name=name,
                kind="view" if kind.strip() == "V" else "table",
                row_estimate=None if estimate is None else int(estimate),
                comment=comment,
                definition=definition,
                columns=tuple(columns.get(oid, [])),
                constraints=tuple(constraints.get(oid, [])),
                indexes=tuple(indexes.get(oid, [])),
            )
            for schema, name, oid, kind, estimate, comment, definition in table_rows
        )
        return SourceCatalog(tables=tables, routines=routines, schemas=schemas)

    def profile(self, schema: str, table: str, column: str) -> ColumnProfile:
        self._require_allowed(schema)
        col = _quote(column)
        statement = (
            f"SELECT COUNT_BIG(*), COUNT_BIG(*) - COUNT_BIG({col}), COUNT_BIG(DISTINCT {col})"
            f" FROM {_quote(schema)}.{_quote(table)}"
        )
        with self._session() as conn:
            cur = conn.cursor()
            cur.execute(statement)
            total, nulls, distinct = cur.fetchone()
        return ColumnProfile(row_count=total, null_count=nulls, distinct_count=distinct)

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
            "sqlserver",
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
        """Run one statement. Defence in depth, not a SQL parser: one ``SELECT`` / ``WITH``
        statement without ``INTO``, and text naming a schema outside the allowed list (or a
        ``sys`` / ``INFORMATION_SCHEMA`` catalog, another database or a remote rowset) is
        refused. Unqualified names resolve in the login's default schema.

        NOT SAFE TO EXPOSE: no route may reach this until the sqlglot statement guard and
        the function allow-list of spec §6.5 exist."""
        text = sql_text.strip().rstrip(";").strip()
        if not text or ";" in text:
            raise ConnectorError("invalid_query", "Send exactly one SQL statement.")
        if not _STARTS_READ_ONLY.match(text) or _INTO.search(text):
            raise ConnectorError("read_only", "DAWAM only reads from source databases.")
        if _CATALOG_REFERENCE.search(text):
            raise ScopeError()
        for name in self._schemas_outside_scope():
            if re.search(rf"(?i)(?<![\w$])(\[{re.escape(name)}\]|{re.escape(name)})\s*\.", text):
                raise ScopeError()
        return self._run(text, limit)

    def _schemas_outside_scope(self) -> list[str]:
        with self._session() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sys.schemas WHERE name NOT IN %s", (self._in_scope(),))
            return [row[0] for row in cur.fetchall()]

    def _run(self, statement: str, limit: int) -> QueryResult:
        with self._session() as conn:
            cur = conn.cursor()
            cur.execute(statement)
            names = tuple(d[0] for d in cur.description or ())
            rows = cur.fetchmany(limit + 1)
        return QueryResult(
            columns=names, rows=tuple(tuple(r) for r in rows[:limit]), truncated=len(rows) > limit
        )
