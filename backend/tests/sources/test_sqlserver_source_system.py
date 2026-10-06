"""A SQL Server Source System through the HTTP API (ticket #49): an owner connects it and
an editor extracts it exactly like a PostgreSQL one (see ``test_connections`` and
``test_extraction``, whose helpers these tests reuse)."""

from __future__ import annotations

import logging

import pytest

from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources.test_extraction import (
    add_system,
    connect,
    extract,
    latest,
    snapshots,
    tables_of,
)


@pytest.fixture
def source(sample_source_sqlserver: SampleSource) -> SampleSource:
    return sample_source_sqlserver


def test_a_test_reports_the_server_the_schemas_and_the_write_warning(
    roles: RoleClients, source: SampleSource
):
    path = add_system(roles) + "/connection"
    owner = roles.client("owner")

    result = owner.post(f"{path}/test", json=source.connection_body()).json()
    writer = owner.post(f"{path}/test", json=source.connection_body(user="writer")).json()

    assert result["ok"] is True and result["server_version"]
    assert {"core", "crm", "restricted"} <= set(result["available_schemas"])
    assert result["missing_schemas"] == [] and result["can_write"] is False
    assert writer["can_write"] is True


@pytest.mark.parametrize(
    "override", [{"password": "wrong-password"}, {"database": "no_such_database"}, {"port": 1}]
)
def test_a_failed_test_gives_a_clear_error_that_leaks_nothing(
    roles: RoleClients, source: SampleSource, override, caplog
):
    path = add_system(roles) + "/connection"
    body = source.connection_body(**override)

    with caplog.at_level(logging.DEBUG):
        response = roles.client("owner").post(f"{path}/test", json=body)

    result = response.json()
    # The exact code is covered in test_sqlserver_connector: SQL Server reports a missing
    # database as a refused login, and pymssql can carry the previous failure's code over.
    assert result["ok"] is False and result["error_code"] and result["error"]
    for secret in (str(body["password"]), source.host, str(body["username"])):
        assert secret not in response.text
        assert secret not in caplog.text


def test_an_editor_extracts_the_full_catalog_into_a_snapshot(
    roles: RoleClients, source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, source.connection_body())

    job = extract(roles, system)

    assert (job["type"], job["status"]) == ("extract", "succeeded")
    [summary] = snapshots(roles, system)
    assert (summary["table_count"], summary["routine_count"]) == (7, 2)
    content = latest(roles, system)
    assert [s["name"] for s in content["db_schemas"]] == ["core", "crm"]
    tables = tables_of(content)
    assert set(tables) == {
        ("core", "accounts"),
        ("core", "branches"),
        ("core", "customer_balances"),
        ("core", "customers"),
        ("core", "transactions"),
        ("core", "عملاء_محليون"),
        ("crm", "contacts"),
    }
    customers = tables["core", "customers"]
    assert (customers["kind"], customers["comment"]) == ("table", "One row per bank customer")
    fk = {c["name"]: c for c in customers["constraints"]}["customers_branch_fk"]
    assert fk["ref_table_id"] == tables["core", "branches"]["id"]
    view = tables["core", "customer_balances"]
    assert view["kind"] == "view" and "JOIN" in view["view_definition"]
    routines = {r["name"]: r for r in content["routines"]}
    assert (routines["account_turnover"]["kind"], routines["close_account"]["kind"]) == (
        "function",
        "procedure",
    )
    assert "JOIN" in routines["account_turnover"]["definition"]
    assert "JOIN" in routines["close_account"]["definition"]
    assert "salaries" not in str(content)

    assert extract(roles, system)["status"] == "succeeded"
    assert len(snapshots(roles, system)) == 1
