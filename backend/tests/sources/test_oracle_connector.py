"""The Oracle Connector against the seeded sample source on Oracle (spec story 40, §8).

Oracle containers are slow and heavy, so these tests are marked ``oracle``: the default
run skips them and CI runs them in a job of their own (``pytest -m oracle``). The
Connector is used directly, as in ``test_postgres_connector``; the extraction test goes
through the HTTP API like every extraction test.
"""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.connector import (
    ConnectionParams,
    ConnectorError,
    ScopeError,
    connector_for,
)
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sample_source.oracle import ALLOWED_SCHEMAS, oracle_body
from tests.sources.test_extraction import add_system, connect, extract, latest, tables_of

pytestmark = pytest.mark.oracle


def connector(source: SampleSource, *, schemas=ALLOWED_SCHEMAS, user="reader", **options):
    username, password = {"reader": source.reader, "writer": source.writer, "admin": source.admin}[
        user
    ]
    return connector_for(
        "oracle",
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


def test_a_test_reports_the_version_visible_schemas_and_no_write_privilege(
    oracle_source: SampleSource,
):
    result = connector(oracle_source, schemas=("CORE", "NOPE")).test()

    assert result.ok and result.error is None
    assert result.server_version and result.server_version[0].isdigit()
    assert {"CORE", "CRM", "RESTRICTED"} <= set(result.available_schemas)
    assert "SYS" not in result.available_schemas
    assert result.missing_schemas == ("NOPE",)
    assert result.can_write is False


def test_a_test_warns_when_the_user_can_write(oracle_source: SampleSource):
    assert connector(oracle_source, user="writer").test().can_write is True
    assert connector(oracle_source, user="admin").test().can_write is True


def test_list_schemas_returns_only_allowed_ones_that_exist(oracle_source: SampleSource):
    assert connector(oracle_source, schemas=("CORE", "NOPE", "CRM")).list_schemas() == (
        "CORE",
        "CRM",
    )


def test_extract_reads_tables_views_columns_keys_and_routines_of_allowed_schemas_only(
    oracle_source: SampleSource,
):
    catalog = connector(oracle_source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    assert ("RESTRICTED", "SALARIES") not in tables
    assert catalog.schemas == ("CORE", "CRM")
    assert {name for _, name in tables} == {
        "ACCOUNTS",
        "BRANCHES",
        "CUSTOMER_BALANCES",
        "CUSTOMERS",
        "TRANSACTIONS",
        "عملاء_محليون",
        "CONTACTS",
    }
    arabic = tables[("CORE", "عملاء_محليون")]
    assert [c.name for c in arabic.columns] == ["رقم", "الاسم", "المدينة"]
    customers = tables[("CORE", "CUSTOMERS")]
    assert customers.kind == "table"
    assert customers.comment == "One row per bank customer"
    assert customers.row_estimate == 3
    by_name = {c.name: c for c in customers.columns}
    assert [c.name for c in customers.columns][:2] == ["CUST_NO", "FULL_NAME"]
    assert by_name["CUST_NO"].is_pk and not by_name["CUST_NO"].is_nullable
    assert by_name["CUST_NO"].data_type == "NUMBER(10)"
    assert by_name["FULL_NAME"].data_type == "VARCHAR2(200)"
    assert not by_name["EMAIL"].is_pk and by_name["EMAIL"].is_nullable
    assert by_name["NATIONAL_ID"].comment == "National ID number"
    balance = {c.name: c for c in tables[("CORE", "ACCOUNTS")].columns}["BALANCE"]
    assert (balance.data_type, balance.default) == ("NUMBER(14,2)", "0")
    view = tables[("CORE", "CUSTOMER_BALANCES")]
    assert view.kind == "view" and "JOIN" in (view.definition or "").upper()
    routines = {(r.kind, r.name): r for r in catalog.routines}
    assert set(routines) == {("function", "ACCOUNT_TURNOVER"), ("procedure", "CLOSE_ACCOUNT")}
    function = routines[("function", "ACCOUNT_TURNOVER")]
    assert "JOIN" in (function.definition or "").upper()
    assert function.signature == "P_ACCT NUMBER"


def test_extract_reads_constraints_and_indexes(oracle_source: SampleSource):
    catalog = connector(oracle_source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    customers = tables[("CORE", "CUSTOMERS")]
    constraints = {c.name: c for c in customers.constraints}
    assert (constraints["CUSTOMERS_PKEY"].type, constraints["CUSTOMERS_PKEY"].columns) == (
        "pk",
        ("CUST_NO",),
    )
    fk = constraints["CUSTOMERS_BRANCH_FK"]
    assert (fk.type, fk.columns, fk.ref_schema, fk.ref_table, fk.ref_columns) == (
        "fk",
        ("BRANCH_CODE",),
        "CORE",
        "BRANCHES",
        ("BRANCH_CODE",),
    )
    unique = {c.name: c for c in tables[("CORE", "BRANCHES")].constraints}["BRANCHES_NAME_UQ"]
    assert (unique.type, unique.columns) == ("unique", ("BRANCH_NAME",))
    indexes = {i.name: i for i in customers.indexes}
    assert indexes["CUSTOMERS_PKEY"].is_unique and indexes["CUSTOMERS_PKEY"].columns == ("CUST_NO",)
    [expression] = indexes["CUSTOMERS_LOWER_EMAIL_IDX"].columns
    assert expression.upper().replace('"', "") == "LOWER(EMAIL)"
    assert not indexes["CUSTOMERS_LOWER_EMAIL_IDX"].is_unique
    assert {i.name for i in tables[("CORE", "ACCOUNTS")].indexes} == {
        "ACCOUNTS_PKEY",
        "ACCOUNTS_CUST_NO_IDX",
    }
    assert tables[("CORE", "CUSTOMER_BALANCES")].constraints == ()


def test_profile_and_sample_stay_inside_the_allowed_schemas(oracle_source: SampleSource):
    source = connector(oracle_source)

    profile = source.profile("CORE", "CUSTOMERS", "EMAIL")
    assert (profile.row_count, profile.null_count, profile.distinct_count) == (3, 1, 2)
    sample = source.sample("CORE", "CUSTOMERS", limit=2)
    assert sample.columns[0] == "CUST_NO" and len(sample.rows) == 2 and sample.truncated
    with pytest.raises(ScopeError):
        source.sample("RESTRICTED", "SALARIES")
    with pytest.raises(ScopeError):
        source.profile("RESTRICTED", "SALARIES", "SALARY")


def test_query_runs_inside_the_scope(oracle_source: SampleSource):
    result = connector(oracle_source).query(
        "SELECT c.full_name FROM customers c JOIN accounts a ON a.cust_no = c.cust_no ORDER BY 1;",
        limit=100,
    )
    assert result.rows == (("Amira Hassan",), ("Amira Hassan",), ("محمد علي",))
    assert not result.truncated


@pytest.mark.parametrize(
    "text",
    [
        "SELECT * FROM restricted.salaries",
        'SELECT * FROM "RESTRICTED"."SALARIES"',
        "SELECT * FROM sys . user$",
        "SELECT username FROM all_users",
        "SELECT * FROM dba_tables",
        "SELECT * FROM v$session",
    ],
)
def test_query_refuses_anything_outside_the_allowed_schemas(oracle_source: SampleSource, text):
    with pytest.raises(ScopeError):
        connector(oracle_source).query(text)


def test_query_allows_one_statement_and_never_writes(oracle_source: SampleSource):
    source = connector(oracle_source, user="admin")
    with pytest.raises(ConnectorError):
        source.query("SELECT 1 FROM dual; DELETE FROM core.accounts")
    with pytest.raises(ConnectorError) as raised:
        source.query("DELETE FROM core.accounts")
    assert raised.value.code == "read_only"
    assert source.query("SELECT count(*) FROM core.accounts").rows == ((3,),)


def test_a_slow_statement_is_cancelled_by_the_timeout(oracle_source: SampleSource):
    source = connector(oracle_source, statement_timeout_seconds=1)

    with pytest.raises(ConnectorError) as raised:
        source.query(
            "SELECT count(*) FROM (SELECT level FROM dual CONNECT BY level <= 10000) a,"
            " (SELECT level FROM dual CONNECT BY level <= 10000) b,"
            " (SELECT level FROM dual CONNECT BY level <= 10000) c"
        )

    assert raised.value.code == "timeout"


def test_errors_do_not_leak_connection_details(oracle_source: SampleSource):
    params = ConnectionParams(
        host=oracle_source.host,
        port=oracle_source.port,
        database=oracle_source.database,
        username="DAWAM_READER",
        password="not-the-password",
        allowed_schemas=("CORE",),
    )

    result = connector_for("oracle", params).test()

    assert not result.ok and result.error_code == "authentication_failed"
    assert "DAWAM_READER" not in (result.error or "")
    assert oracle_source.host not in (result.error or "")
    assert "not-the-password" not in repr(params)


def test_an_unknown_service_is_reported_safely(oracle_source: SampleSource):
    params = ConnectionParams(
        host=oracle_source.host,
        port=oracle_source.port,
        database="NOSUCHSERVICE",
        username="DAWAM_READER",
        password="reader-secret",
        allowed_schemas=("CORE",),
    )

    result = connector_for("oracle", params).test()

    assert not result.ok and result.error_code == "database_not_found"


def test_an_editor_extracts_an_oracle_source_into_a_snapshot(
    roles: RoleClients, oracle_source: SampleSource
):
    system = add_system(roles)
    body = oracle_body(oracle_source)
    tested = roles.client("owner").post(f"{system}/connection/test", json=body)
    assert tested.json()["ok"] is True, tested.text
    connect(roles, system, body)

    job = extract(roles, system)

    assert (job["type"], job["status"]) == ("extract", "succeeded"), job
    assert "7 tables and views" in job["log"] and "2 routines" in job["log"]
    content = latest(roles, system)
    assert [s["name"] for s in content["db_schemas"]] == ["CORE", "CRM"]
    tables = tables_of(content)
    assert ("CORE", "CUSTOMER_BALANCES") in tables
    assert tables["CORE", "CUSTOMER_BALANCES"]["kind"] == "view"
    assert "JOIN" in tables["CORE", "CUSTOMER_BALANCES"]["view_definition"].upper()
    fk = {c["name"]: c for c in tables["CORE", "CUSTOMERS"]["constraints"]}["CUSTOMERS_BRANCH_FK"]
    assert fk["ref_table_id"] == tables["CORE", "BRANCHES"]["id"]
    routines = {(r["kind"], r["name"]): r for r in content["routines"]}
    assert set(routines) == {("function", "ACCOUNT_TURNOVER"), ("procedure", "CLOSE_ACCOUNT")}
    assert "SUM" in routines["function", "ACCOUNT_TURNOVER"]["definition"].upper()
