"""The Oracle Connector (spec story 40).

An Oracle Database Schema is a database user: ``allowed_schemas`` are owner names, given
exactly as Oracle stores them (usually upper case), and ``database`` is the service name.
The driver is python-oracledb in thin mode (no Oracle client install).

Every session is read-only (``SET TRANSACTION READ ONLY``), has a call timeout (default
30 s, which cancels the running statement) and a ``CURRENT_SCHEMA`` of the first allowed
schema. Errors are reduced to a fixed set of safe messages: the driver's text can name the
host, the user or a path.
"""

from __future__ import annotations

import re
import ssl
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import oracledb

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

_CONSTRAINT_TYPES = {"P": "pk", "R": "fk", "U": "unique"}
_CATALOG_REFERENCE = re.compile(
    r"(?i)(?<![\w$#])(?:(?:dba|cdb)_|(?:all|user)_(?:tab|col|cons|ind|obj|users|view|sour|syn"
    r"|seq|proc|arg|mview|trig|dep|db_link|role|sys|priv)|v\$|gv\$|sys\.|system\.)[\w$#]*"
)
_AUTH_CODES = {"ORA-01017", "ORA-28000", "ORA-28001", "ORA-01005"}
_NOT_FOUND_CODES = {"ORA-12514", "ORA-12505", "ORA-12154", "DPY-6001", "DPY-6002"}
_TIMEOUT_CODES = {"ORA-01013", "DPY-4024", "DPY-4011"}


def _error_for(exc: oracledb.Error) -> ConnectorError:
    """A safe, fixed message for what went wrong (never the driver's text)."""
    detail = exc.args[0] if exc.args else None
    code = str(getattr(detail, "full_code", "") or "")
    if code == "DPY-6005":
        # A failed connect wraps the real cause in its message (classified, never shown).
        text = str(exc)
        code = next((c for c in _NOT_FOUND_CODES | _AUTH_CODES if c in text), code)
    if code in _AUTH_CODES:
        return ConnectorError(
            "authentication_failed", "The database rejected the username or password."
        )
    if code in _NOT_FOUND_CODES:
        return ConnectorError(
            "database_not_found", "The service name is not known to that database server."
        )
    if code in _TIMEOUT_CODES:
        return ConnectorError(
            "timeout", "The database did not answer within the statement timeout."
        )
    if code == "ORA-01456":
        return ConnectorError("read_only", "DAWAM only reads from source databases.")
    if code in ("ORA-01031", "ORA-00942"):
        return ConnectorError(
            "permission_denied", "The database user lacks a needed privilege or the object."
        )
    if code.startswith(("ORA-009", "ORA-01722", "ORA-01756")):
        return ConnectorError("invalid_query", "The database could not run that query.")
    if code.startswith(("DPY-6", "DPY-3", "ORA-125", "ORA-124")) or isinstance(
        exc, oracledb.OperationalError
    ):
        return ConnectorError(
            "connection_failed",
            "Could not connect to the database server. Check the host, port and network access.",
        )
    return ConnectorError("database_error", "The database reported an error.")


def _option_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lob_as_text(cursor: oracledb.Cursor, metadata: Any) -> Any:
    """Fetch CLOB/BLOB values as ``str``/``bytes`` instead of lazy LOB handles."""
    if metadata.type_code is oracledb.DB_TYPE_CLOB:
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if metadata.type_code is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    return None


def _type_name(
    data_type: str,
    char_length: int | None,
    precision: int | None,
    scale: int | None,
) -> str:
    if data_type in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR") and char_length:
        return f"{data_type}({char_length})"
    if data_type == "NUMBER" and precision is not None:
        return f"NUMBER({precision},{scale})" if scale else f"NUMBER({precision})"
    return data_type


class OracleConnector:
    def __init__(self, params: ConnectionParams) -> None:
        self._params = params
        self._timeout_s = _option_int(
            params.options, "statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS
        )

    # -- sessions ---------------------------------------------------------------------

    def _tls(self) -> dict[str, Any]:
        mode = self._params.options.get("sslmode")
        if mode not in ("require", "verify-ca", "verify-full"):
            return {}
        context = ssl.create_default_context()
        if mode != "verify-full":
            context.check_hostname = False
        if mode == "require":
            context.verify_mode = ssl.CERT_NONE
        return {"protocol": "tcps", "ssl_context": context}

    @contextmanager
    def _session(self) -> Iterator[oracledb.Connection]:
        params = self._params
        try:
            connection = oracledb.connect(
                user=params.username,
                password=params.password or None,
                host=params.host,
                port=params.port,
                service_name=params.database,
                tcp_connect_timeout=float(
                    _option_int(params.options, "connect_timeout", DEFAULT_CONNECT_TIMEOUT_SECONDS)
                ),
                **self._tls(),
            )
        except oracledb.Error as exc:
            raise _error_for(exc) from None
        try:
            connection.call_timeout = self._timeout_s * 1000
            connection.outputtypehandler = _lob_as_text
            connection.action = "dawam"
            try:
                with connection.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    if params.allowed_schemas:
                        first = _quote(params.allowed_schemas[0])
                        cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {first}")
                yield connection
            except oracledb.Error as exc:
                raise _error_for(exc) from None
        finally:
            with suppress(oracledb.Error):
                connection.close()

    def _binds(self) -> tuple[str, dict[str, str]]:
        names = {f"s{i}": name for i, name in enumerate(self._params.allowed_schemas)}
        return ", ".join(f":{key}" for key in names), names

    def _require_allowed(self, schema: str) -> None:
        if schema not in self._params.allowed_schemas:
            raise ScopeError()

    # -- the Connector interface --------------------------------------------------------

    def test(self) -> ConnectionTest:
        in_list, binds = self._binds()
        try:
            with self._session() as conn, conn.cursor() as cur:
                version = str(conn.version)
                cur.execute(
                    "SELECT DISTINCT o.owner FROM all_objects o"
                    " JOIN all_users u ON u.username = o.owner"
                    " WHERE u.oracle_maintained = 'N' ORDER BY o.owner"
                )
                available = tuple(row[0] for row in cur.fetchall())
                allowed = self._params.allowed_schemas
                missing = tuple(name for name in allowed if name not in available)
                cur.execute(
                    f"""
                    SELECT CASE WHEN
                      EXISTS (SELECT 1 FROM session_privs WHERE privilege IN (
                        'CREATE TABLE', 'CREATE ANY TABLE', 'INSERT ANY TABLE', 'UPDATE ANY TABLE',
                        'DELETE ANY TABLE', 'DROP ANY TABLE', 'ALTER ANY TABLE', 'CREATE PROCEDURE',
                        'CREATE ANY PROCEDURE', 'EXECUTE ANY PROCEDURE'))
                      OR EXISTS (SELECT 1 FROM all_users WHERE username IN ({in_list})
                                 AND username = SYS_CONTEXT('USERENV', 'SESSION_USER'))
                      OR EXISTS (SELECT 1 FROM all_tab_privs
                                 WHERE table_schema IN ({in_list})
                                   AND privilege IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER')
                                   AND (grantee = SYS_CONTEXT('USERENV', 'SESSION_USER')
                                        OR grantee = 'PUBLIC'))
                      OR EXISTS (SELECT 1 FROM role_tab_privs
                                 WHERE owner IN ({in_list})
                                   AND privilege IN ('INSERT', 'UPDATE', 'DELETE', 'ALTER'))
                    THEN 1 ELSE 0 END FROM dual
                    """,
                    binds,
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
        in_list, binds = self._binds()
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT username FROM all_users WHERE username IN ({in_list})", binds)
            existing = {row[0] for row in cur.fetchall()}
        return tuple(name for name in self._params.allowed_schemas if name in existing)

    def extract(self) -> SourceCatalog:
        in_list, binds = self._binds()
        with self._session() as conn, conn.cursor() as cur:
            cur.arraysize = 500
            cur.execute(
                f"SELECT username FROM all_users WHERE username IN ({in_list}) ORDER BY username",
                binds,
            )
            schemas = tuple(row[0] for row in cur.fetchall())
            cur.execute(
                f"""
                SELECT c.owner, c.table_name, c.table_type, t.num_rows, c.comments
                FROM all_tab_comments c
                LEFT JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
                WHERE c.owner IN ({in_list}) AND c.table_type IN ('TABLE', 'VIEW')
                  AND c.table_name NOT LIKE 'BIN$%'
                  AND (t.table_name IS NULL OR (t.dropped = 'NO' AND t.nested = 'NO'))
                  AND NOT EXISTS (SELECT 1 FROM all_mviews m
                                  WHERE m.owner = c.owner AND m.mview_name = c.table_name
                                    AND c.table_type = 'TABLE')
                ORDER BY c.owner, c.table_name
                """,
                binds,
            )
            table_rows = cur.fetchall()
            cur.execute(
                f"SELECT owner, view_name, text FROM all_views WHERE owner IN ({in_list})", binds
            )
            view_sql = {(owner, name): text for owner, name, text in cur.fetchall()}
            cur.execute(
                f"""
                SELECT c.owner, c.table_name, c.column_name, c.column_id, c.data_type,
                       c.char_length, c.data_precision, c.data_scale, c.nullable,
                       c.data_default, m.comments
                FROM all_tab_columns c
                LEFT JOIN all_col_comments m ON m.owner = c.owner
                  AND m.table_name = c.table_name AND m.column_name = c.column_name
                WHERE c.owner IN ({in_list})
                ORDER BY c.owner, c.table_name, c.column_id
                """,
                binds,
            )
            raw_columns = cur.fetchall()
            cur.execute(
                f"""
                SELECT c.owner, c.table_name, c.constraint_name, c.constraint_type,
                       c.r_owner, c.r_constraint_name
                FROM all_constraints c
                WHERE c.owner IN ({in_list}) AND c.constraint_type IN ('P', 'R', 'U')
                ORDER BY c.owner, c.table_name, c.constraint_name
                """,
                binds,
            )
            constraint_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT owner, constraint_name, table_name, column_name
                FROM all_cons_columns
                WHERE owner IN ({in_list}) OR (owner, constraint_name) IN (
                  SELECT r_owner, r_constraint_name FROM all_constraints
                  WHERE owner IN ({in_list}) AND constraint_type = 'R')
                ORDER BY owner, constraint_name, position
                """,
                binds,
            )
            constraint_columns: dict[tuple[str, str], tuple[str, list[str]]] = {}
            for owner, name, table, column in cur.fetchall():
                constraint_columns.setdefault((owner, name), (table, []))[1].append(column)
            cur.execute(
                f"""
                SELECT i.table_owner, i.table_name, i.owner, i.index_name, i.uniqueness
                FROM all_indexes i
                WHERE i.table_owner IN ({in_list}) AND i.index_type <> 'LOB'
                ORDER BY i.table_owner, i.table_name, i.index_name
                """,
                binds,
            )
            index_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT c.index_owner, c.index_name, c.column_name, e.column_expression
                FROM all_ind_columns c
                LEFT JOIN all_ind_expressions e ON e.index_owner = c.index_owner
                  AND e.index_name = c.index_name AND e.column_position = c.column_position
                WHERE c.table_owner IN ({in_list})
                ORDER BY c.index_owner, c.index_name, c.column_position
                """,
                binds,
            )
            index_columns: dict[tuple[str, str], list[str]] = {}
            for owner, name, column, expression in cur.fetchall():
                text = str(expression).strip() if expression is not None else column
                index_columns.setdefault((owner, name), []).append(text)
            cur.execute(
                f"""
                SELECT owner, object_name, object_type FROM all_objects
                WHERE owner IN ({in_list}) AND object_type IN ('FUNCTION', 'PROCEDURE')
                ORDER BY owner, object_name
                """,
                binds,
            )
            routine_rows = cur.fetchall()
            cur.execute(
                f"""
                SELECT owner, name, type, text FROM all_source
                WHERE owner IN ({in_list}) AND type IN ('FUNCTION', 'PROCEDURE')
                ORDER BY owner, name, type, line
                """,
                binds,
            )
            source: dict[tuple[str, str], list[str]] = {}
            for owner, name, _kind, text in cur.fetchall():
                source.setdefault((owner, name), []).append(text or "")
            cur.execute(
                f"""
                SELECT owner, object_name, argument_name, in_out, data_type
                FROM all_arguments
                WHERE owner IN ({in_list}) AND package_name IS NULL
                  AND argument_name IS NOT NULL AND data_level = 0
                ORDER BY owner, object_name, position
                """,
                binds,
            )
            arguments: dict[tuple[str, str], list[str]] = {}
            for owner, name, argument, direction, data_type in cur.fetchall():
                prefix = "" if direction == "IN" else f"{direction.replace('/', ' ')} "
                arguments.setdefault((owner, name), []).append(f"{argument} {prefix}{data_type}")

        primary_keys = {
            (owner, table): set(constraint_columns.get((owner, name), ("", []))[1])
            for owner, table, name, kind, _ro, _rc in constraint_rows
            if kind == "P"
        }
        columns: dict[tuple[str, str], list[ColumnInfo]] = {}
        for (
            owner,
            table,
            name,
            ordinal,
            dtype,
            clen,
            prec,
            scale,
            nullable,
            default,
            note,
        ) in raw_columns:
            default_text = str(default).strip() if default is not None else None
            columns.setdefault((owner, table), []).append(
                ColumnInfo(
                    name,
                    ordinal,
                    _type_name(dtype, clen, prec, scale),
                    nullable == "Y",
                    name in primary_keys.get((owner, table), ()),
                    default_text or None,
                    note,
                )
            )
        constraints: dict[tuple[str, str], list[ConstraintInfo]] = {}
        for owner, table, name, kind, ref_owner, ref_name in constraint_rows:
            _, cols = constraint_columns.get((owner, name), (table, []))
            ref_table, ref_cols = (
                constraint_columns.get((ref_owner, ref_name), (None, []))
                if kind == "R"
                else (None, [])
            )
            constraints.setdefault((owner, table), []).append(
                ConstraintInfo(
                    name=name,
                    type=_CONSTRAINT_TYPES[kind],
                    columns=tuple(cols),
                    ref_schema=ref_owner if kind == "R" else None,
                    ref_table=ref_table,
                    ref_columns=tuple(ref_cols),
                )
            )
        indexes: dict[tuple[str, str], list[IndexInfo]] = {}
        for table_owner, table, owner, name, uniqueness in index_rows:
            indexes.setdefault((table_owner, table), []).append(
                IndexInfo(name, tuple(index_columns.get((owner, name), [])), uniqueness == "UNIQUE")
            )
        tables = tuple(
            TableInfo(
                schema=owner,
                name=name,
                kind="view" if kind == "VIEW" else "table",
                row_estimate=estimate,
                comment=comment,
                definition=view_sql.get((owner, name)) if kind == "VIEW" else None,
                columns=tuple(columns.get((owner, name), [])),
                constraints=tuple(constraints.get((owner, name), [])),
                indexes=tuple(indexes.get((owner, name), [])),
            )
            for owner, name, kind, estimate, comment in table_rows
        )
        routines = tuple(
            RoutineInfo(
                owner,
                name,
                "procedure" if kind == "PROCEDURE" else "function",
                "CREATE OR REPLACE " + "".join(source[owner, name])
                if (owner, name) in source
                else None,
                ", ".join(arguments.get((owner, name), [])),
            )
            for owner, name, kind in routine_rows
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
            "oracle",
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
        with a current schema of the first allowed schema, one statement only, and text
        naming a schema outside the allowed list (or a ``DBA_``/``ALL_``/``V$`` catalog
        view) is refused. Give the Connection's database user no more than read access.

        NOT SAFE TO EXPOSE: no route may reach this until the sqlglot statement guard and
        the function allow-list of spec §6.5 exist."""
        text = sql_text.strip().rstrip(";").strip()
        if not text or ";" in text:
            raise ConnectorError("invalid_query", "Send exactly one SQL statement.")
        if _CATALOG_REFERENCE.search(text):
            raise ScopeError()
        for name in self._schemas_outside_scope():
            if re.search(rf'(?i)(?<![\w$#])"?{re.escape(name)}"?\s*\.', text):
                raise ScopeError()
        return self._run(text, limit)

    def _schemas_outside_scope(self) -> list[str]:
        in_list, binds = self._binds()
        with self._session() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT username FROM all_users WHERE username NOT IN ({in_list})", binds)
            return [row[0] for row in cur.fetchall()]

    def _run(self, statement: str, limit: int) -> QueryResult:
        with self._session() as conn, conn.cursor() as cur:
            cur.arraysize = min(limit + 1, 500)
            cur.execute(statement)
            names = tuple(d[0] for d in cur.description or ())
            rows = cur.fetchmany(limit + 1)
        return QueryResult(
            columns=names, rows=tuple(tuple(r) for r in rows[:limit]), truncated=len(rows) > limit
        )
