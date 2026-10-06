"""The MySQL / MariaDB Connector against the seeded sample source on both servers
(spec story 40, §8). Like the PostgreSQL tests, it uses the Connector directly."""

from __future__ import annotations

import pymysql
import pytest

from dawam.modules.sources.internal.connector import (
    ConnectionParams,
    ConnectorError,
    ScopeError,
    connector_for,
)
from tests.sample_source.mysql import MySqlSource


def connector(source: MySqlSource, *, schemas=("core", "crm"), user="reader", **options):
    username, password = {"reader": source.reader, "writer": source.writer, "admin": source.admin}[
        user
    ]
    return connector_for(
        "mysql",
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


def test_test_reports_version_schemas_and_no_write_privilege_for_a_reader(
    mysql_source: MySqlSource,
):
    result = connector(mysql_source, schemas=("core", "nope")).test()

    assert result.ok and result.server_version
    assert ("mariadb" in result.server_version.lower()) == mysql_source.is_mariadb
    assert {"core", "crm", "restricted"} <= set(result.available_schemas)
    assert "mysql" not in result.available_schemas
    assert result.missing_schemas == ("nope",)
    assert result.can_write is False


def test_the_write_privilege_warning_for_a_writer_and_an_admin(mysql_source: MySqlSource):
    assert connector(mysql_source, user="writer").test().can_write is True
    assert connector(mysql_source, user="admin").test().can_write is True
    # The writer's grant is on core only: crm alone is clean.
    assert connector(mysql_source, user="writer", schemas=("crm",)).test().can_write is False


def test_list_schemas_returns_only_allowed_ones_that_exist(mysql_source: MySqlSource):
    assert connector(mysql_source, schemas=("core", "nope", "crm")).list_schemas() == (
        "core",
        "crm",
    )


def test_extract_reads_tables_views_columns_keys_and_routines_of_allowed_schemas_only(
    mysql_source: MySqlSource,
):
    catalog = connector(mysql_source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    assert ("restricted", "salaries") not in tables
    assert catalog.schemas == ("core", "crm")
    assert {schema for schema, _ in tables} == {"core", "crm"}
    assert ("core", "عملاء_محليون") in tables
    assert [c.name for c in tables[("core", "عملاء_محليون")].columns] == ["رقم", "الاسم", "المدينة"]
    customers = tables[("core", "customers")]
    assert customers.kind == "table"
    assert customers.comment == "One row per bank customer"
    assert customers.definition is None
    by_name = {c.name: c for c in customers.columns}
    assert by_name["cust_no"].is_pk and not by_name["cust_no"].is_nullable
    assert not by_name["email"].is_pk and by_name["email"].is_nullable
    assert by_name["email"].default is None
    assert by_name["national_id"].comment == "National ID number"
    assert by_name["email"].comment is None
    assert float(tables[("core", "accounts")].columns[3].default or "x") == 0  # "0.00" on MySQL
    view = tables[("core", "customer_balances")]
    assert view.kind == "view" and "join" in (view.definition or "").lower()
    assert view.comment is None and view.row_estimate is None
    routines = {(r.kind, r.name): r for r in catalog.routines}
    assert set(routines) == {("function", "account_turnover"), ("procedure", "close_account")}
    assert "join" in (routines[("function", "account_turnover")].definition or "").lower()
    assert "join" in (routines[("procedure", "close_account")].definition or "").lower()


def test_extract_reads_constraints_indexes_estimates_and_routine_signatures(
    mysql_source: MySqlSource,
):
    catalog = connector(mysql_source).extract()

    tables = {(t.schema, t.name): t for t in catalog.tables}
    customers = tables[("core", "customers")]
    constraints = {c.name: c for c in customers.constraints}
    assert (constraints["PRIMARY"].type, constraints["PRIMARY"].columns) == ("pk", ("cust_no",))
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
    assert indexes["PRIMARY"].is_unique and indexes["PRIMARY"].columns == ("cust_no",)
    assert not indexes["customers_email_idx"].is_unique
    assert indexes["customers_email_idx"].columns == ("email",)
    assert {i.name for i in tables[("core", "accounts")].indexes} == {
        "PRIMARY",
        "accounts_cust_no_idx",
    }
    assert tables[("core", "customer_balances")].constraints == ()
    assert all(t.row_estimate is None or t.row_estimate >= 0 for t in catalog.tables)
    routines = {r.name: r for r in catalog.routines}
    assert routines["account_turnover"].signature.startswith("p_acct int")


def test_extract_names_a_functional_index_by_its_expression(mysql_source: MySqlSource):
    if mysql_source.is_mariadb:
        pytest.skip("MariaDB has no functional indexes")
    with (
        pymysql.connect(
            host=mysql_source.host,
            port=mysql_source.port,
            user=mysql_source.admin[0],
            password=mysql_source.admin[1],
            autocommit=True,
        ) as raw,
        raw.cursor() as cur,
    ):
        cur.execute("ALTER TABLE crm.contacts ADD COLUMN tag varchar(50)")
        cur.execute("CREATE INDEX contacts_lower_tag_idx ON crm.contacts ((lower(tag)))")
    tables = {
        (t.schema, t.name): t for t in connector(mysql_source, schemas=("crm",)).extract().tables
    }

    indexes = {i.name: i for i in tables[("crm", "contacts")].indexes}
    assert indexes["contacts_lower_tag_idx"].columns == ("lower(tag)",)


def test_profile_and_sample_stay_inside_the_allowed_schemas(mysql_source: MySqlSource):
    source = connector(mysql_source)

    profile = source.profile("core", "customers", "email")
    assert (profile.row_count, profile.null_count, profile.distinct_count) == (3, 1, 2)
    sample = source.sample("core", "customers", limit=2)
    assert sample.columns[0] == "cust_no" and len(sample.rows) == 2 and sample.truncated
    with pytest.raises(ScopeError):
        source.sample("restricted", "salaries")
    with pytest.raises(ScopeError):
        source.profile("restricted", "salaries", "salary")


def test_query_runs_inside_the_scope(mysql_source: MySqlSource):
    result = connector(mysql_source).query(
        "SELECT c.full_name FROM customers c JOIN accounts a ON a.cust_no = c.cust_no ORDER BY 1;",
        limit=100,
    )
    assert result.rows == (("Amira Hassan",), ("Amira Hassan",), ("محمد علي",))
    assert not result.truncated


@pytest.mark.parametrize(
    "text",
    [
        "SELECT * FROM restricted.salaries",
        "SELECT * FROM `restricted`.`salaries`",
        "SELECT * FROM restricted . salaries",
        "SELECT user FROM mysql.user",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM `performance_schema`.threads",
    ],
)
def test_query_refuses_anything_outside_the_allowed_schemas(mysql_source: MySqlSource, text):
    with pytest.raises(ScopeError):
        connector(mysql_source).query(text)


def test_query_allows_one_statement_and_never_writes(mysql_source: MySqlSource):
    source = connector(mysql_source, user="admin")
    with pytest.raises(ConnectorError):
        source.query("SELECT 1; DELETE FROM core.accounts")
    for text in ("DELETE FROM core.accounts", "DROP TABLE core.accounts"):
        with pytest.raises(ConnectorError) as raised:
            source.query(text)
        assert raised.value.code == "read_only"
    assert source.query("SELECT count(*) FROM core.accounts").rows == ((3,),)


def test_the_database_itself_refuses_writes_in_the_read_only_transaction(
    mysql_source: MySqlSource,
):
    source = connector(mysql_source, user="admin")

    with (
        pytest.raises(ConnectorError) as raised,
        source._session() as conn,  # type: ignore[attr-defined]
        conn.cursor() as cur,
    ):
        cur.execute("DELETE FROM core.accounts")

    assert raised.value.code == "read_only"
    assert source.query("SELECT count(*) FROM core.accounts").rows == ((3,),)


def test_a_slow_statement_is_cancelled_by_the_timeout(mysql_source: MySqlSource):
    source = connector(mysql_source, statement_timeout_seconds=1)

    with pytest.raises(ConnectorError) as raised:
        source.query("SELECT SLEEP(30) FROM customers")

    assert raised.value.code == "timeout"


def test_the_session_has_the_default_schema_and_timeout(mysql_source: MySqlSource):
    source = connector(mysql_source)

    if mysql_source.is_mariadb:
        rows = source.query("SELECT @@max_statement_time, DATABASE()").rows
        assert (float(rows[0][0]), rows[0][1]) == (30.0, "core")
    else:
        assert source.query("SELECT @@max_execution_time, DATABASE()").rows == ((30000, "core"),)


def test_errors_do_not_leak_connection_details(mysql_source: MySqlSource):
    params = ConnectionParams(
        host=mysql_source.host,
        port=mysql_source.port,
        database=mysql_source.database,
        username="dawam_reader",
        password="not-the-password",
        allowed_schemas=("core",),
    )

    result = connector_for("mysql", params).test()

    assert not result.ok and result.error_code == "authentication_failed"
    assert "dawam_reader" not in (result.error or "")
    assert "not-the-password" not in repr(params)
    assert mysql_source.host not in repr(params)


def test_an_unreachable_server_is_a_safe_connection_failure():
    params = ConnectionParams(
        host="127.0.0.1",
        port=1,
        database="core",
        username="u",
        password="p",
        allowed_schemas=("core",),
        options={"connect_timeout": 2},
    )

    result = connector_for("mysql", params).test()

    assert not result.ok and result.error_code == "connection_failed"
    assert "127.0.0.1" not in (result.error or "")
