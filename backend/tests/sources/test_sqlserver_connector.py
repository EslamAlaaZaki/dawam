"""The SQL Server Connector against the seeded sample source database (spec story 40, §8).

The same checks as ``test_postgres_connector``, through the Connector interface directly.
SQL Server has no read-only transaction or per-session search path, so the read-only and
scope checks are the Connector's own (see ``internal/sqlserver.py``).
"""

from __future__ import annotations

import pymssql
import pytest

from dawam.modules.sources.internal.connector import (
    ConnectionParams,
    ConnectorError,
    ScopeError,
    connector_for,
)
from dawam.modules.sources.internal.sqlserver import _error_for
from tests.sample_source import SampleSource


@pytest.fixture
def source(sample_source_sqlserver: SampleSource) -> SampleSource:
    return sample_source_sqlserver


def connector(source: SampleSource, *, schemas=("core", "crm"), user="reader", **options):
    username, password = {"reader": source.reader, "writer": source.writer, "admin": source.admin}[
        user
    ]
    return connector_for(
        "sqlserver",
        ConnectionParams(
            host=source.host,
            port=source.port,
            database=source.database,
            username=username,
            password=password,
            allowed_schemas=tuple(schemas),
            options=dict(options),
        ),
    )


def test_test_reports_version_schemas_and_the_write_privilege_warning(source: SampleSource):
    result = connector(source).test()

    assert result.ok and result.server_version
    assert result.can_write is False
    assert {"core", "crm", "restricted"} <= set(result.available_schemas)
    assert "sys" not in result.available_schemas and "db_owner" not in result.available_schemas
    assert result.missing_schemas == ()
    assert connector(source, schemas=("core", "nope")).test().missing_schemas == ("nope",)
    assert connector(source, user="writer").test().can_write is True
    assert connector(source, user="admin").test().can_write is True


def test_list_schemas_returns_only_allowed_ones_that_exist(source: SampleSource):
    assert connector(source, schemas=("core", "nope", "crm")).list_schemas() == ("core", "crm")


def test_extract_reads_tables_views_columns_keys_and_routines_of_allowed_schemas_only(
    source: SampleSource,
):
    catalog = connector(source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    assert ("restricted", "salaries") not in tables
    assert catalog.schemas == ("core", "crm")
    assert {schema for schema, _ in tables} == {"core", "crm"}
    assert [c.name for c in tables[("core", "عملاء_محليون")].columns] == ["رقم", "الاسم", "المدينة"]
    customers = tables[("core", "customers")]
    assert customers.kind == "table"
    assert customers.comment == "One row per bank customer"
    by_name = {c.name: c for c in customers.columns}
    assert by_name["cust_no"].is_pk and not by_name["cust_no"].is_nullable
    assert not by_name["email"].is_pk and by_name["email"].is_nullable
    assert by_name["email"].data_type == "nvarchar(200)"
    assert by_name["national_id"].comment == "National ID number"
    accounts = tables[("core", "accounts")]
    assert accounts.columns[3].default == "0"
    assert accounts.columns[3].data_type == "decimal(14,2)"
    view = tables[("core", "customer_balances")]
    assert view.kind == "view" and "JOIN" in (view.definition or "")
    assert [c.name for c in view.columns] == ["cust_no", "full_name", "total_balance"]
    routines = {(r.kind, r.name): r for r in catalog.routines}
    assert set(routines) == {("function", "account_turnover"), ("procedure", "close_account")}
    assert "JOIN" in (routines[("function", "account_turnover")].definition or "")
    assert "JOIN" in (routines[("procedure", "close_account")].definition or "")


def test_extract_reads_constraints_indexes_estimates_and_routine_signatures(source: SampleSource):
    catalog = connector(source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    customers = tables[("core", "customers")]
    constraints = {c.name: c for c in customers.constraints}
    assert constraints["customers_pkey"].type == "pk"
    assert constraints["customers_pkey"].columns == ("cust_no",)
    fk = constraints["customers_branch_fk"]
    assert (fk.type, fk.columns, fk.ref_schema, fk.ref_table, fk.ref_columns) == (
        "fk",
        ("branch_code",),
        "core",
        "branches",
        ("branch_code",),
    )
    unique = {c.name: c for c in tables[("core", "branches")].constraints}["branches_name_uq"]
    assert (unique.type, unique.columns) == ("unique", ("branch_name",))
    indexes = {i.name: i for i in customers.indexes}
    assert indexes["customers_pkey"].is_unique and indexes["customers_pkey"].columns == ("cust_no",)
    assert indexes["customers_email_idx"].columns == ("email",)
    assert not indexes["customers_email_idx"].is_unique
    assert {i.name for i in tables[("core", "accounts")].indexes} == {
        "accounts_pkey",
        "accounts_cust_no_idx",
    }
    assert tables[("core", "customer_balances")].constraints == ()
    assert tables[("core", "accounts")].row_estimate == 3
    assert all(t.row_estimate is None or t.row_estimate >= 0 for t in catalog.tables)
    routines = {r.name: r for r in catalog.routines}
    assert routines["account_turnover"].signature == "@p_acct int"


def test_profile_and_sample_stay_inside_the_allowed_schemas(source: SampleSource):
    reader = connector(source)

    profile = reader.profile("core", "customers", "email")
    assert (profile.row_count, profile.null_count, profile.distinct_count) == (3, 1, 2)
    sample = reader.sample("core", "customers", limit=2)
    assert sample.columns[0] == "cust_no" and len(sample.rows) == 2 and sample.truncated
    with pytest.raises(ScopeError):
        reader.sample("restricted", "salaries")
    with pytest.raises(ScopeError):
        reader.profile("restricted", "salaries", "salary")


def test_query_runs_inside_the_scope(source: SampleSource):
    result = connector(source).query(
        "SELECT c.full_name FROM customers c JOIN accounts a ON a.cust_no = c.cust_no ORDER BY 1;",
        limit=100,
    )
    assert result.rows == (("Amira Hassan",), ("Amira Hassan",), ("محمد علي",))
    assert not result.truncated


@pytest.mark.parametrize(
    "text",
    [
        "SELECT * FROM restricted.salaries",
        "SELECT * FROM [restricted].[salaries]",
        "SELECT * FROM restricted . salaries",
        "SELECT name FROM sys.objects",
        "SELECT * FROM INFORMATION_SCHEMA.TABLES",
        "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x', 'SELECT 1')",
    ],
)
def test_query_refuses_anything_outside_the_allowed_schemas(source: SampleSource, text):
    with pytest.raises(ScopeError):
        connector(source).query(text)


def test_query_allows_one_read_statement_and_never_writes(source: SampleSource):
    admin = connector(source, user="admin")
    with pytest.raises(ConnectorError):
        admin.query("SELECT 1; DELETE FROM core.accounts")
    for text in (
        "DELETE FROM core.accounts",
        "EXEC core.close_account 100",
        "SELECT 1 INTO core.t",
    ):
        with pytest.raises(ConnectorError) as raised:
            admin.query(text)
        assert raised.value.code == "read_only"
    assert admin.query("SELECT count(*) FROM core.accounts").rows == ((3,),)


def test_a_slow_statement_is_cancelled_by_the_timeout(source: SampleSource):
    reader = connector(source, statement_timeout_seconds=1)

    with pytest.raises(ConnectorError) as raised:
        reader.query(
            "WITH n AS (SELECT 1 AS x UNION ALL SELECT x + 1 FROM n WHERE x < 2000000000)"
            " SELECT count(*) FROM n OPTION (MAXRECURSION 0)"
        )

    assert raised.value.code == "timeout"


def test_errors_do_not_leak_connection_details(source: SampleSource):
    params = ConnectionParams(
        host=source.host,
        port=source.port,
        database=source.database,
        username="dawam_reader",
        password="not-the-password",
        allowed_schemas=("core",),
    )

    result = connector_for("sqlserver", params).test()

    assert not result.ok and result.error_code
    assert "dawam_reader" not in (result.error or "")
    assert "not-the-password" not in repr(params)
    assert source.host not in repr(params)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (((18456, b"Login failed for user 'x'."),), "authentication_failed"),
        ((18456, b"DB-Lib error message 20018"), "authentication_failed"),
        (((4060, b"Cannot open database"),), "database_not_found"),
        (((20003, b"Adaptive Server connection timed out"),), "timeout"),
        (((20009, b"Unable to connect: TDS server is unavailable"),), "connection_failed"),
    ],
)
def test_driver_errors_become_safe_fixed_messages(args, code):
    error = _error_for(pymssql.OperationalError(*args))

    assert error.code == code
    assert "TDS" not in error.message and "Login" not in error.message
