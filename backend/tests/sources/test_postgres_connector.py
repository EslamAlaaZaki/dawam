"""The PostgreSQL Connector against the seeded sample source database (spec story 40, §8).

The Connector has no HTTP surface yet (extraction, profiling and AI exploration, which
call it, come in later tickets), so these tests use it directly: the one place tests
touch ``sources`` internals.
"""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.connector import (
    ConnectionParams,
    ConnectorError,
    ScopeError,
    connector_for,
)
from tests.sample_source import SampleSource


def connector(source: SampleSource, *, schemas=("core", "crm"), user="reader", **options):
    username, password = {"reader": source.reader, "writer": source.writer, "admin": source.admin}[
        user
    ]
    return connector_for(
        "postgresql",
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


def test_an_unsupported_engine_is_refused(sample_source: SampleSource):
    with pytest.raises(ConnectorError) as raised:
        connector_for("db2", connector(sample_source)._params)  # type: ignore[attr-defined]
    assert raised.value.code == "unsupported_engine"


def test_list_schemas_returns_only_allowed_ones_that_exist(sample_source: SampleSource):
    assert connector(sample_source, schemas=("core", "nope", "crm")).list_schemas() == (
        "core",
        "crm",
    )


def test_extract_reads_tables_views_columns_keys_and_routines_of_allowed_schemas_only(
    sample_source: SampleSource,
):
    catalog = connector(sample_source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    assert ("restricted", "salaries") not in tables
    assert catalog.schemas == ("core", "crm")
    assert {schema for schema, _ in tables} == {"core", "crm"}
    assert ("core", "عملاء_محليون") in tables
    assert [c.name for c in tables[("core", "عملاء_محليون")].columns] == ["رقم", "الاسم", "المدينة"]
    customers = tables[("core", "customers")]
    assert customers.kind == "table"
    assert customers.comment == "One row per bank customer"
    by_name = {c.name: c for c in customers.columns}
    assert by_name["cust_no"].is_pk and not by_name["cust_no"].is_nullable
    assert not by_name["email"].is_pk and by_name["email"].is_nullable
    assert by_name["national_id"].comment == "National ID number"
    assert tables[("core", "accounts")].columns[3].default == "0"
    view = tables[("core", "customer_balances")]
    assert view.kind == "view" and "JOIN" in (view.definition or "")
    routines = {(r.kind, r.name): r for r in catalog.routines}
    assert set(routines) == {("function", "account_turnover"), ("procedure", "close_account")}
    assert "JOIN" in (routines[("function", "account_turnover")].definition or "")


def test_extract_reads_constraints_indexes_estimates_and_routine_signatures(
    sample_source: SampleSource,
):
    catalog = connector(sample_source).extract()

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
    assert indexes["customers_lower_email_idx"].columns == ("lower(email)",)
    assert not indexes["customers_lower_email_idx"].is_unique
    assert {i.name for i in tables[("core", "accounts")].indexes} == {
        "accounts_pkey",
        "accounts_cust_no_idx",
    }
    assert tables[("core", "customer_balances")].constraints == ()
    assert all(t.row_estimate is None or t.row_estimate >= 0 for t in catalog.tables)
    routines = {r.name: r for r in catalog.routines}
    assert routines["account_turnover"].signature == "p_acct integer"


def test_profile_and_sample_stay_inside_the_allowed_schemas(sample_source: SampleSource):
    source = connector(sample_source)

    profile = source.profile("core", "customers", "email")
    assert (profile.row_count, profile.null_count, profile.distinct_count) == (3, 1, 2)
    sample = source.sample("core", "customers", limit=2)
    assert sample.columns[0] == "cust_no" and len(sample.rows) == 2 and sample.truncated
    with pytest.raises(ScopeError):
        source.sample("restricted", "salaries")
    with pytest.raises(ScopeError):
        source.profile("restricted", "salaries", "salary")


def test_query_runs_inside_the_scope(sample_source: SampleSource):
    result = connector(sample_source).query(
        "SELECT c.full_name FROM customers c JOIN accounts a ON a.cust_no = c.cust_no ORDER BY 1;",
        limit=100,
    )
    assert result.rows == (("Amira Hassan",), ("Amira Hassan",), ("محمد علي",))
    assert not result.truncated


@pytest.mark.parametrize(
    "text",
    [
        "SELECT * FROM restricted.salaries",
        'SELECT * FROM "restricted"."salaries"',
        "SELECT * FROM public . foo",
        "SELECT rolname FROM pg_roles",
        "SELECT * FROM information_schema.tables",
    ],
)
def test_query_refuses_anything_outside_the_allowed_schemas(sample_source: SampleSource, text):
    with pytest.raises(ScopeError):
        connector(sample_source).query(text)


def test_query_allows_one_statement_and_never_writes(sample_source: SampleSource):
    source = connector(sample_source, user="admin")
    with pytest.raises(ConnectorError):
        source.query("SELECT 1; DELETE FROM core.accounts")
    with pytest.raises(ConnectorError) as raised:
        source.query("DELETE FROM core.accounts")
    assert raised.value.code == "read_only"
    assert source.query("SELECT count(*) FROM core.accounts").rows == ((3,),)


def test_a_slow_statement_is_cancelled_by_the_timeout(sample_source: SampleSource):
    source = connector(sample_source, statement_timeout_seconds=1)

    with pytest.raises(ConnectorError) as raised:
        source.query("SELECT count(*) FROM generate_series(1, 100000000000)")

    assert raised.value.code == "timeout"


def test_the_session_is_read_only_with_the_default_timeout(sample_source: SampleSource):
    source = connector(sample_source)

    rows = source.query(
        "SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')"
    ).rows

    assert rows == (("on", "30s"),)


def test_errors_do_not_leak_connection_details(sample_source: SampleSource):
    params = ConnectionParams(
        host=sample_source.host,
        port=sample_source.port,
        database=sample_source.database,
        username="dawam_reader",
        password="not-the-password",
        allowed_schemas=("core",),
    )

    result = connector_for("postgresql", params).test()

    assert not result.ok
    assert "dawam_reader" not in (result.error or "")
    assert "not-the-password" not in repr(params)
    assert sample_source.host not in repr(params)
