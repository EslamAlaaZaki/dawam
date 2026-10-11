"""Hashing a confirmed PII column and PII handling notes (spec §6.12, story 138).

Uses the lineage suite's Workspace: source ``crm.dbo.orders`` (amount, status) is staged,
Core ``fact_orders.amount_c`` reads the staged amount and Mart ``mart_orders.amount_m``
reads the Core amount.
"""

from __future__ import annotations

import re

import pytest
import sqlalchemy as sa

from dawam.modules.warehouse.pii_hashing import SALT_PLACEHOLDER, hashed_sql, unhashed_sql
from dawam.modules.warehouse.platforms import TARGET_PLATFORMS
from tests.lineage import test_lineage
from tests.lineage.test_lineage import confirm_pii, dw, ok, pii_view
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import columns_of, query

graph = test_lineage.graph  # the lineage suite's Workspace (a fixture)

_KNOWN_LITERALS = {"'UTF8'", "'hex'", "'SHA2_256'", "'SHA256'"}
"""The string literals a hashed expression may hold: encodings and algorithm names."""


def literals(sql: str) -> set[str]:
    return set(re.findall(r"'(?:[^']|'')*'", sql))


def mapping_path(roles: RoleClients, column_id: str) -> str:
    return f"{dw(roles)}/staging/columns/{column_id}/mapping"


def set_mapping(roles: RoleClients, column_id: str, mapping_type: str, version: int, **extra):
    return roles.client("editor").put(
        mapping_path(roles, column_id),
        json={"mapping_type": mapping_type, "version": version, **extra},
    )


def hash_column(roles: RoleClients, column_id: str, version: int = 1) -> dict:
    return ok(set_mapping(roles, column_id, "hashed", version))


@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_the_hashed_expression_reads_the_salt_from_a_placeholder(platform):
    sql = hashed_sql(platform, 'dbo.orders."Amount"')

    assert sql.count(SALT_PLACEHOLDER) == 1
    assert literals(sql) <= _KNOWN_LITERALS
    assert "256" in sql
    assert unhashed_sql(platform, sql) == 'dbo.orders."Amount"'


def test_unhashing_refuses_an_expression_it_did_not_make():
    with pytest.raises(ValueError):
        unhashed_sql("postgresql", "dbo.orders.amount")


def test_a_confirmed_pii_column_is_hashed_and_stays_pii_derived(graph, roles):
    confirm_pii(roles, graph["src_amount"])

    body = hash_column(roles, graph["stg_amount"])

    assert body["mapping_type"] == "hashed"
    assert body["version"] == 2
    assert body["sql_expression"] == (
        "encode(sha256(convert_to(:pii_salt || CAST(dbo.orders.amount AS TEXT), 'UTF8')), 'hex')"
    )
    assert ok(roles.client("viewer").get(mapping_path(roles, graph["stg_amount"]))) == body
    columns = pii_view(roles)["columns"]
    assert [(c["label"], c["pseudonymised"]) for c in columns] == [
        (f"{graph['stg']['name']}.amount", True),
        ("fact_orders.amount_c", True),
        ("mart_orders.amount_m", True),
    ]


def test_only_the_hashed_column_and_what_it_feeds_are_pseudonymised(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    confirm_pii(roles, graph["src_status"])
    hash_column(roles, graph["stg_status"])

    columns = {c["label"]: c["pseudonymised"] for c in pii_view(roles)["columns"]}

    assert columns == {
        f"{graph['stg']['name']}.amount": False,
        f"{graph['stg']['name']}.status": True,
        "fact_orders.amount_c": False,
        "mart_orders.amount_m": False,
    }


def test_a_column_that_is_not_confirmed_pii_cannot_be_hashed(graph, roles):
    response = set_mapping(roles, graph["stg_amount"], "hashed", 1)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_mapping"


def test_mapping_it_direct_again_restores_the_source_column(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    hash_column(roles, graph["stg_amount"])

    body = ok(set_mapping(roles, graph["stg_amount"], "direct", 2))

    assert body["sql_expression"] == "dbo.orders.amount"
    assert body["version"] == 3
    assert not any(c["pseudonymised"] for c in pii_view(roles)["columns"])


def test_a_stale_version_is_a_conflict(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    hash_column(roles, graph["stg_amount"])

    response = set_mapping(roles, graph["stg_amount"], "direct", 1)

    assert response.status_code == 409, response.text


def test_only_a_staging_column_fed_by_the_source_has_this_mapping(graph, roles):
    for column_id in (graph["amount_c"], columns_of(roles, graph["stg"])["load_ts"]["id"]):
        response = roles.client("viewer").get(mapping_path(roles, column_id))
        assert response.status_code == 404, response.text


def test_a_core_column_cannot_be_mapped_as_hashed(graph, roles):
    response = roles.client("editor").put(
        f"{dw(roles)}/tables/{graph['core']}/mapping/columns/{graph['amount_c']}",
        json={"mapping_type": "hashed", "sql_expression": "x", "version": 1},
    )

    assert response.status_code == 422, response.text


def test_hashed_mappings_never_contain_a_literal_salt(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    confirm_pii(roles, graph["src_status"])

    # A salt sent along is not a field of the request: it is never read, let alone stored.
    hash_column(roles, graph["stg_amount"])
    ok(set_mapping(roles, graph["stg_status"], "hashed", 1, salt="s3cr3t-salt"))

    hashed = query(
        roles, "select sql_expression from column_mappings where mapping_type = 'hashed'"
    )
    assert len(hashed) == 2
    for (sql,) in hashed:
        assert SALT_PLACEHOLDER in sql
        assert literals(sql) <= _KNOWN_LITERALS
    stored = query(
        roles,
        "select 1 from column_mappings where sql_expression like '%s3cr3t%'"
        " union all select 1 from audit_entries where cast(new as text) like '%s3cr3t%'",
    )
    assert stored == []


def test_hashing_is_audited(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    hash_column(roles, graph["stg_amount"])

    [(old, new)] = query(
        roles,
        "select old, new from audit_entries where entity_type = 'column_mapping'"
        " and new ->> 'mapping_type' = 'hashed'",
    )

    assert old["mapping_type"] == "direct"
    assert new["mapping_type"] == "hashed"


# --- handling notes ----------------------------------------------------------------------


def column_path(roles: RoleClients, table_id: str, column_id: str) -> str:
    return f"{dw(roles)}/tables/{table_id}/columns/{column_id}"


def column(roles: RoleClients, table_id: str, column_id: str) -> dict:
    columns = ok(roles.client("viewer").get(f"{dw(roles)}/tables/{table_id}"))["columns"]
    return next(c for c in columns if c["id"] == column_id)


def set_note(roles: RoleClients, table_id: str, column_id: str, note: str | None):
    version = column(roles, table_id, column_id)["version"]
    return roles.client("editor").patch(
        column_path(roles, table_id, column_id), json={"version": version, "pii_note": note}
    )


def test_a_pii_derived_column_takes_a_handling_note(graph, roles):
    confirm_pii(roles, graph["src_amount"])

    body = ok(set_note(roles, graph["mart"], graph["amount_m"], "Aggregate before sharing."))

    assert body["pii_note"] == "Aggregate before sharing."
    notes = {c["label"]: c["pii_note"] for c in pii_view(roles)["columns"]}
    assert notes["mart_orders.amount_m"] == "Aggregate before sharing."
    assert notes["fact_orders.amount_c"] is None


def test_a_staging_column_takes_a_handling_note_too(graph, roles):
    confirm_pii(roles, graph["src_amount"])

    body = ok(set_note(roles, graph["stg"]["id"], graph["stg_amount"], "Hash before loading."))

    assert body["pii_note"] == "Hash before loading."


def test_a_column_that_is_not_pii_derived_takes_no_note(graph, roles):
    response = set_note(roles, graph["mart"], graph["amount_m"], "Not personal data.")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "pii_note"


def test_a_note_is_cleared_with_null(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    ok(set_note(roles, graph["mart"], graph["amount_m"], "Aggregate before sharing."))

    body = ok(set_note(roles, graph["mart"], graph["amount_m"], None))

    assert body["pii_note"] is None


def test_a_hashed_expression_of_another_platform_is_refused_not_a_crash(graph, roles):
    confirm_pii(roles, graph["src_amount"])
    hash_column(roles, graph["stg_amount"])
    with roles.app.state.engine.begin() as connection:
        connection.execute(sa.text("update data_warehouses set target_platform = 'sqlserver'"))

    response = set_mapping(roles, graph["stg_amount"], "direct", 2)

    assert response.status_code == 422, response.text
