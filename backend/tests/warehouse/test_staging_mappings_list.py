"""The Staging Layer's mappings in one read, and Staging in the mapping sheet export (#646).

Uses the lineage suite's Workspace: source ``crm.dbo.orders`` (amount numeric(12,2), status
varchar(8)) is staged."""

from __future__ import annotations

import csv
import io

import sqlalchemy as sa

from tests.lineage import test_lineage
from tests.lineage.test_lineage import accept_pii, dw, ok
from tests.roles import RoleClients
from tests.warehouse.test_mapping_export import book
from tests.warehouse.test_pii_hashing import hash_column

graph = test_lineage.graph


def listing(roles: RoleClients, role: str = "viewer") -> dict:
    return ok(roles.client(role).get(f"{dw(roles)}/staging/mappings"))


def run(roles: RoleClients, sql: str) -> None:
    with roles.app.state.engine.begin() as connection:
        connection.execute(sa.text(sql))


def test_each_staging_table_lists_its_columns_with_their_source(graph, roles):
    body = listing(roles)

    [table] = body["tables"]
    assert table["table_id"] == graph["stg"]["id"]
    assert table["status"] == "present"
    assert (table["system_code"], table["system_name"]) == ("crm", "crm")
    assert (table["source_db_schema"], table["source_table_name"]) == ("dbo", "orders")
    columns = {c["column_name"]: c for c in table["columns"]}
    assert [c["column_name"] for c in table["columns"]][:2] == ["amount", "status"]
    amount = columns["amount"]
    assert amount["mapping_type"] == "direct"
    assert amount["source_column_name"] == "amount"
    assert amount["source_data_type"] == "numeric(12,2)"
    assert amount["data_type"]["type"] == "decimal"
    assert amount["is_system"] is False
    assert amount["status"] == "present"
    assert amount["review_flags"] == []
    audit = [c for c in table["columns"] if c["is_system"]]
    assert len(audit) == 2
    for c in audit:
        assert c["mapping_type"] == "system"
        assert c["source_column_name"] is None
        assert c["source_data_type"] is None
    assert body["totals"] == {
        "tables": 1,
        "columns": 4,
        "direct": 2,
        "hashed": 0,
        "audit": 2,
        "to_review": 0,
        "pii": 0,
    }
    assert amount["is_pii"] is False
    assert amount["mapping_version"] == 1


def test_a_hashed_column_is_counted_as_hashed(graph, roles):
    accept_pii(roles, graph["src_status"])
    hash_column(roles, graph["stg_status"])

    body = listing(roles)

    columns = {c["column_name"]: c for c in body["tables"][0]["columns"]}
    assert columns["status"]["mapping_type"] == "hashed"
    assert columns["amount"]["mapping_type"] == "direct"
    assert (body["totals"]["direct"], body["totals"]["hashed"]) == (1, 1)
    # Accepted PII is marked, with the version the next switch sends.
    assert (columns["status"]["is_pii"], columns["status"]["mapping_version"]) == (True, 2)
    assert columns["amount"]["is_pii"] is False
    assert body["totals"]["pii"] == 1


def test_review_flags_and_removed_sources_count_as_to_review(graph, roles):
    run(
        roles,
        'update dw_columns set review_flags = cast(\'[{"code": "lossy_type",'
        ' "message": "m"}]\' as json) where id = \'' + graph["stg_amount"] + "'",
    )
    run(
        roles, f"update dw_columns set status = 'source_removed' where id = '{graph['stg_status']}'"
    )
    run(roles, f"update dw_tables set status = 'source_removed' where id = '{graph['stg']['id']}'")

    body = listing(roles)

    [table] = body["tables"]
    assert table["status"] == "source_removed"
    columns = {c["column_name"]: c for c in table["columns"]}
    assert columns["amount"]["review_flags"] == [{"code": "lossy_type", "message": "m"}]
    assert columns["status"]["status"] == "source_removed"
    assert body["totals"]["to_review"] == 2


def test_only_the_staging_layer_is_listed(graph, roles):
    assert [t["table_name"] for t in listing(roles)["tables"]] == [graph["stg"]["name"]]


def test_a_non_member_cannot_list_them(graph, roles):
    response = roles.client("non_member").get(f"{dw(roles)}/staging/mappings")

    assert response.status_code in (403, 404), response.text


def test_the_staging_sheet_has_a_row_per_staging_column(graph, roles):
    accept_pii(roles, graph["src_status"])
    hash_column(roles, graph["stg_status"])

    response = roles.client("viewer").get(f"{dw(roles)}/mapping-sheet", params={"layer": "staging"})

    assert response.status_code == 200, response.text
    assert "mapping-sheet-staging.xlsx" in response.headers["content-disposition"]
    sheets = book(response.content)
    assert sheets["Branches"] == []
    rows = {r["target_column"]: r for r in sheets["Mapping"]}
    assert len(rows) == 4
    amount = rows["amount"]
    assert amount["layer"] == "staging"
    assert amount["target_table"] == graph["stg"]["name"]
    assert amount["input_system"] == "crm"
    assert amount["input_database_schema"] == "dbo"
    assert amount["input_table"] == "orders"
    assert amount["input_column(s)"] == "dbo.orders.amount"
    assert amount["sql_expression"] == "dbo.orders.amount"
    assert amount["mapping_type"] == "direct"
    assert amount["target_type"]
    assert rows["status"]["mapping_type"] == "hashed"
    assert ":pii_salt" in rows["status"]["sql_expression"]
    system = [r for r in rows.values() if r["mapping_type"] == "system"]
    assert len(system) == 2
    assert all(r["input_column(s)"] in (None, "") for r in system)


def test_the_staging_sheet_as_csv_and_saved_to_the_file_area(graph, roles):
    response = roles.client("viewer").get(
        f"{dw(roles)}/mapping-sheet", params={"layer": "staging", "format": "csv"}
    )

    assert response.status_code == 200, response.text
    assert "mapping-sheet-staging.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert {r["layer"] for r in rows} == {"staging"}
    assert len(rows) == 4
    saved = roles.client("editor").post(
        f"{dw(roles)}/files/mapping-sheet", params={"format": "csv", "layer": "staging"}
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["name"] == "mapping-sheet-staging.csv"


def test_the_default_sheet_still_leaves_staging_out(graph, roles):
    rows = book(roles.client("viewer").get(f"{dw(roles)}/mapping-sheet").content)["Mapping"]

    assert {r["layer"] for r in rows} == {"core", "mart"}
