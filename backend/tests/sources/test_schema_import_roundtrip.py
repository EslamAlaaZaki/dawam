"""Schema Import round trip, per connectable engine (spec §6.4 and its test plan).

The helper queries run on the seeded sample source; their results are filled into the
template (as one CSV per sheet) and imported. The imported catalog must equal what live
extraction reads, and the HTTP import after an extraction must be a no-op ("unchanged"),
i.e. the two Snapshots are the same apart from profile data.

Oracle is marked ``oracle`` (slow container, own CI job); MySQL runs on MySQL and MariaDB.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import fields, replace

import psycopg
import pymssql
import pymysql
import pytest

from dawam.modules.sources.internal.connector import (
    ConnectionParams,
    SourceCatalog,
    connector_for,
)
from dawam.modules.sources.internal.schema_import import SHEETS, read_catalog
from dawam.modules.sources.internal.schema_import_queries import helper_query
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sample_source.mysql import MySqlSource
from tests.sample_source.oracle import ALLOWED_SCHEMAS as ORACLE_SCHEMAS
from tests.sample_source.oracle import oracle_body
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_schema_import import upload

Runner = Callable[[str], tuple[Sequence[str], list[Sequence]]]
"""Runs one query on the source: its column names and rows."""


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def fill_template(engine: str, schemas: tuple[str, ...], run: Runner) -> list[tuple]:
    """Run every sheet's query and write its result as a CSV, as an Editor would."""
    files = []
    for sheet in SHEETS:
        names, rows = run(helper_query(engine, sheet.name, schemas))
        assert tuple(names) == sheet.column_names, f"{engine} {sheet.name}"
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(names)
        writer.writerows([_cell(v) for v in row] for row in rows)
        files.append(("files", (f"{sheet.name}.csv", out.getvalue().encode(), "text/csv")))
    return files


def _normal(catalog: SourceCatalog) -> SourceCatalog:
    """Order that the databases leave to their collation or to chance."""
    tables = tuple(
        replace(
            t,
            constraints=tuple(sorted(t.constraints, key=lambda c: c.name)),
            indexes=tuple(sorted(t.indexes, key=lambda i: i.name)),
        )
        for t in sorted(catalog.tables, key=lambda t: (t.schema, t.name))
    )
    routines = tuple(sorted(catalog.routines, key=lambda r: (r.schema, r.name, r.signature)))
    return SourceCatalog(tables=tables, routines=routines, schemas=tuple(sorted(catalog.schemas)))


def differences(imported: SourceCatalog, live: SourceCatalog) -> list[str]:
    """What differs, readably (imported vs live), table by table and routine by routine."""
    found = []
    if imported.schemas != live.schemas:
        found.append(f"schemas {imported.schemas} != {live.schemas}")
    for a, b in zip(imported.tables, live.tables, strict=True):
        for field in (f.name for f in fields(a)):
            if getattr(a, field) != getattr(b, field):
                found.append(
                    f"{a.schema}.{a.name}.{field}: {getattr(a, field)!r} != {getattr(b, field)!r}"
                )
    for a, b in zip(imported.routines, live.routines, strict=True):
        for field in (f.name for f in fields(a)):
            if getattr(a, field) != getattr(b, field):
                found.append(
                    f"{a.schema}.{a.name}.{field}: {getattr(a, field)!r} != {getattr(b, field)!r}"
                )
    return found


def assert_round_trip(
    roles: RoleClients,
    engine: str,
    params: ConnectionParams,
    body: dict,
    schemas: tuple[str, ...],
    run: Runner,
) -> None:
    files = fill_template(engine, schemas, run)

    imported, report = read_catalog([(f[1][0], f[1][1]) for f in files])
    assert report.errors == [], report.errors
    assert imported is not None
    live = connector_for("mysql" if engine == "mariadb" else engine, params)
    live_catalog = live.extract()
    assert live_catalog.tables and live_catalog.routines
    assert [t.name for t in _normal(imported).tables] == [
        t.name for t in _normal(live_catalog).tables
    ]
    diff = differences(_normal(imported), _normal(live_catalog))
    assert not diff, chr(10).join(diff)

    system = add_system(roles)
    connect(roles, system, body)
    extract(roles, system)
    again = upload(roles, system, files)
    assert again["imported"] and again["unchanged"], again["report"]


def _params(source, schemas: tuple[str, ...]) -> ConnectionParams:
    username, password = source.reader
    return ConnectionParams(
        host=source.host,
        port=source.port,
        database=source.database,
        username=username,
        password=password,
        allowed_schemas=schemas,
    )


def test_postgresql_round_trip(roles: RoleClients, sample_source: SampleSource):
    schemas = ("core", "crm")
    username, password = sample_source.reader

    def run(query: str):
        with (
            psycopg.connect(
                host=sample_source.host,
                port=sample_source.port,
                dbname=sample_source.database,
                user=username,
                password=password,
                # What a live Connection sees: it qualifies names in view and default text
                # exactly as far as the search path asks.
                options=f"-c search_path={','.join(schemas)}",
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(query)
            return [d.name for d in cur.description or ()], cur.fetchall()

    assert_round_trip(
        roles,
        "postgresql",
        _params(sample_source, schemas),
        sample_source.connection_body(),
        schemas,
        run,
    )


def test_mysql_and_mariadb_round_trip(roles: RoleClients, mysql_source: MySqlSource):
    schemas = ("core", "crm")
    username, password = mysql_source.reader

    def run(query: str):
        with (
            pymysql.connect(
                host=mysql_source.host,
                port=mysql_source.port,
                user=username,
                password=password,
                charset="utf8mb4",
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(query)
            return [d[0] for d in cur.description], list(cur.fetchall())

    engine = "mariadb" if mysql_source.is_mariadb else "mysql"
    assert_round_trip(
        roles,
        engine,
        _params(mysql_source, schemas),
        mysql_source.connection_body(),
        schemas,
        run,
    )


def test_sqlserver_round_trip(roles: RoleClients, sample_source_sqlserver: SampleSource):
    source = sample_source_sqlserver
    schemas = ("core", "crm")
    username, password = source.reader

    def run(query: str):
        with pymssql.connect(
            server=source.host,
            port=str(source.port),
            user=username,
            password=password,
            database=source.database,
            charset="UTF-8",
        ) as conn:
            cur = conn.cursor()
            cur.execute(query)
            return [d[0] for d in cur.description], cur.fetchall()

    assert_round_trip(
        roles,
        "sqlserver",
        _params(source, schemas),
        source.connection_body(engine="sqlserver"),
        schemas,
        run,
    )


@pytest.mark.oracle
def test_oracle_round_trip(roles: RoleClients, oracle_source: SampleSource):
    import oracledb

    source = oracle_source
    username, password = source.reader

    def run(query: str):
        oracledb.defaults.fetch_lobs = False
        with oracledb.connect(
            user=username,
            password=password,
            host=source.host,
            port=source.port,
            service_name=source.database,
        ) as conn:
            cur = conn.cursor()
            cur.execute(query)
            return [d[0] for d in cur.description], cur.fetchall()

    assert_round_trip(
        roles,
        "oracle",
        _params(source, tuple(ORACLE_SCHEMAS)),
        oracle_body(source),
        tuple(ORACLE_SCHEMAS),
        run,
    )
