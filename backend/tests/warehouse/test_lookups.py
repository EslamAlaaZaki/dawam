"""Dimension lookups, system columns and the layering rule (spec §6.14, story 103)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import (
    _staging_table,
    _warehouse_id,
    base,
    column,
    edges,
    get_mapping,
    mapping_path,
    table,
)


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def fk(roles, table_id, name, dimension_id) -> dict:
    response = roles.client("editor").post(
        f"{base(roles)}/tables/{table_id}/columns",
        json={
            "name": name,
            "data_type": {"type": "bigint"},
            "role": "fk",
            "references_table_id": dimension_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def model(warehouse: RoleClients) -> dict:
    """Staging orders; Core ``dim_customer`` (natural key ``customer_code``) and fact
    ``fact_orders`` with an FK to it; Mart ``fact_orders_m`` with its own FK."""
    engine = warehouse.app.state.engine
    _staging_table(
        engine, _warehouse_id(engine), "stg_orders", ["customer_code", "order_date", "amount"]
    )
    dim = table(warehouse, "dim_customer", "core")
    column(warehouse, dim["id"], "customer_code", role="nk")
    fact = table(warehouse, "fact_orders", "core", kind="fact")
    mart_dim = table(warehouse, "dim_customer_m", "mart")
    mart_fact = table(warehouse, "fact_orders_m", "mart", kind="fact")
    return {
        "dim": dim,
        "dim_sk": {"id": system_column(warehouse, dim["id"])["column_id"]},
        "fact": fact,
        "mart_fact": mart_fact,
        "customer_fk": fk(warehouse, fact["id"], "customer_key", dim["id"]),
        "mart_fk": fk(warehouse, mart_fact["id"], "customer_key", mart_dim["id"]),
        "plain": column(warehouse, fact["id"], "note"),
    }


def put_column(roles, table_id, column_id, role="editor", **body):
    path = f"{mapping_path(roles, table_id)}/columns/{column_id}"
    return roles.client(role).put(path, json=body)


def lookup(roles, model, role="editor", **spec):
    spec = {"nk_inputs": ["stg_orders.customer_code"]} | spec
    return put_column(
        roles,
        model["fact"]["id"],
        model["customer_fk"]["id"],
        role,
        mapping_type="lookup",
        lookup=spec,
    )


def fk_view(roles, model) -> dict:
    mapping = get_mapping(roles, model["fact"]["id"])
    return next(c for c in mapping["columns"] if c["column_name"] == "customer_key")


def test_a_core_fact_fk_is_mapped_as_a_lookup_of_the_referenced_dimension(warehouse, model):
    response = lookup(warehouse, model, as_of_input="stg_orders.order_date")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mapping_type"] == "lookup"
    assert body["lookup"]["nk_inputs"] == ["stg_orders.customer_code"]
    assert body["lookup"]["as_of_input"] == "stg_orders.order_date"
    assert body["lookup"]["unknown_key"] == -1
    assert body["lookup"]["dimension_id"] == model["dim"]["id"]
    assert body["lookup"]["dimension_name"] == "dim_customer"
    assert fk_view(warehouse, model)["lookup"]["nk_inputs"] == ["stg_orders.customer_code"]


def test_a_lookup_produces_a_lookup_edge_to_the_dimension_and_uses_edges_for_its_inputs(
    warehouse, model
):
    lookup(warehouse, model, as_of_input="stg_orders.order_date", unknown_key=-9)

    upstream = edges(warehouse, model["customer_fk"]["id"])

    found = {(e["kind"], e["from_label"], e["to_label"]) for e in upstream}
    downstream = edges(warehouse, model["customer_fk"]["id"], "downstream")
    into_dimension = edges(warehouse, model["dim_sk"]["id"])
    assert [(e["kind"], e["to_label"]) for e in downstream] == [("lookup", "dim_customer")]
    assert ("lookup", "fact_orders.customer_key", "dim_customer") in {
        (e["kind"], e["from_label"], e["to_label"]) for e in into_dimension
    }
    assert ("uses", "stg_orders.customer_code", "fact_orders") in found
    assert ("uses", "stg_orders.order_date", "fact_orders") in found
    assert not [e for e in upstream if e["kind"] == "value"]
    view = fk_view(warehouse, model)
    assert view["lookup"]["unknown_key"] == -9
    assert {i["column_name"] for i in view["uses"]} == {"customer_code", "order_date"}


def test_saving_a_lookup_again_replaces_its_edges(warehouse, model):
    lookup(warehouse, model, as_of_input="stg_orders.order_date")

    again = put_column(
        warehouse,
        model["fact"]["id"],
        model["customer_fk"]["id"],
        version=1,
        mapping_type="lookup",
        lookup={"nk_inputs": ["stg_orders.customer_code"]},
    )

    assert again.status_code == 200, again.text
    assert [e["kind"] for e in edges(warehouse, model["customer_fk"]["id"])] == ["uses"]
    downstream = edges(warehouse, model["customer_fk"]["id"], "downstream")
    assert [e["kind"] for e in downstream] == ["lookup"]


def test_the_lookup_shows_the_sql_that_resolves_the_key_with_the_unknown_member(warehouse, model):
    lookup(warehouse, model, unknown_key=-5)

    sql = fk_view(warehouse, model)["sql_expression"]

    assert "dim_customer.customer_code = stg_orders.customer_code" in sql
    assert "dim_customer.dim_customer_key" in sql
    assert sql.rstrip(")").endswith("-5")


def test_an_scd2_dimension_needs_an_as_of_input_and_filters_on_validity(warehouse, model):
    scd2 = table(warehouse, "dim_product", "core")
    column(warehouse, scd2["id"], "product_code", role="nk")
    warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{scd2['id']}", json={"version": 1, "scd_type": 2}
    )
    product_fk = fk(warehouse, model["fact"]["id"], "product_key", scd2["id"])
    path = f"{mapping_path(warehouse, model['fact']['id'])}/columns/{product_fk['id']}"
    spec = {"nk_inputs": ["stg_orders.customer_code"]}

    missing = warehouse.client("editor").put(path, json={"mapping_type": "lookup", "lookup": spec})
    given = warehouse.client("editor").put(
        path,
        json={
            "mapping_type": "lookup",
            "lookup": spec | {"as_of_input": "stg_orders.order_date"},
        },
    )

    assert missing.status_code == 422, missing.text
    assert given.status_code == 200, given.text
    assert (
        "stg_orders.order_date BETWEEN dim_product.scd_valid_from" in given.json()["sql_expression"]
    )


@pytest.mark.parametrize(
    "spec",
    [
        {"nk_inputs": []},
        {"nk_inputs": "stg_orders.customer_code"},
        {"nk_inputs": ["stg_orders.customer_code", "stg_orders.amount"]},
        {"nk_inputs": ["customer_code"]},
        {"nk_inputs": ["UPPER(stg_orders.customer_code)"]},
        {"nk_inputs": ["stg_orders.missing"]},
        {"nk_inputs": ["fact_orders.note"]},
        {"nk_inputs": ["dim_customer.customer_code"]},
        {"nk_inputs": ["stg_orders.customer_code"], "as_of_input": "stg_orders.missing"},
        {"nk_inputs": ["stg_orders.customer_code"], "unknown_key": "x"},
        {"nk_inputs": ["stg_orders.customer_code"], "unknown_key": True},
    ],
)
def test_a_bad_lookup_is_refused(warehouse, model, spec):
    response = put_column(
        warehouse,
        model["fact"]["id"],
        model["customer_fk"]["id"],
        mapping_type="lookup",
        lookup=spec,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_mapping"


def test_only_a_core_fk_column_can_be_a_lookup_and_it_carries_no_sql(warehouse, model):
    not_fk = put_column(
        warehouse,
        model["fact"]["id"],
        model["plain"]["id"],
        mapping_type="lookup",
        lookup={"nk_inputs": ["stg_orders.customer_code"]},
    )
    mart = put_column(
        warehouse,
        model["mart_fact"]["id"],
        model["mart_fk"]["id"],
        mapping_type="lookup",
        lookup={"nk_inputs": ["fact_orders.customer_key"]},
    )
    no_lookup = put_column(
        warehouse, model["fact"]["id"], model["customer_fk"]["id"], mapping_type="lookup"
    )
    with_sql = put_column(
        warehouse,
        model["fact"]["id"],
        model["customer_fk"]["id"],
        mapping_type="lookup",
        sql_expression="1",
        lookup={"nk_inputs": ["stg_orders.customer_code"]},
    )
    stray = put_column(
        warehouse,
        model["fact"]["id"],
        model["plain"]["id"],
        mapping_type="constant",
        sql_expression="'x'",
        lookup={"nk_inputs": ["stg_orders.customer_code"]},
    )

    assert [r.status_code for r in (not_fk, mart, no_lookup, with_sql, stray)] == [422] * 5


def test_a_lookup_names_its_dimension_in_the_layer_of_the_fact_without_breaking_layering(
    warehouse, model
):
    # The dimension is a Core table, the fact's own Layer, yet the lookup is accepted.
    assert lookup(warehouse, model).status_code == 200


def test_a_mart_fk_mapped_direct_from_the_core_fact_passes(warehouse, model):
    response = put_column(
        warehouse,
        model["mart_fact"]["id"],
        model["mart_fk"]["id"],
        mapping_type="direct",
        sql_expression="fact_orders.customer_key",
    )

    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["table_name"] == "fact_orders"


def test_data_inputs_come_only_from_the_layer_directly_below(warehouse, model):
    core_from_core = put_column(
        warehouse,
        model["fact"]["id"],
        model["plain"]["id"],
        mapping_type="direct",
        sql_expression="dim_customer.customer_code",
    )
    mart_from_staging = put_column(
        warehouse,
        model["mart_fact"]["id"],
        model["mart_fk"]["id"],
        mapping_type="direct",
        sql_expression="stg_orders.customer_code",
    )

    assert core_from_core.status_code == 422
    assert mart_from_staging.status_code == 422


def system_column(roles, table_id) -> dict:
    return next(c for c in get_mapping(roles, table_id)["columns"] if c["mapping_type"] == "system")


def test_system_columns_default_to_the_system_type_and_are_saved_at_table_level(warehouse, model):
    sk = system_column(warehouse, model["dim"]["id"])
    assert sk["version"] == 0

    saved = put_column(
        warehouse,
        model["dim"]["id"],
        sk["column_id"],
        mapping_type="system",
        rule_text="Generated by the load",
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["mapping_type"] == "system"
    assert saved.json()["rule_text"] == "Generated by the load"
    assert saved.json()["inputs"] == []
    assert not edges(warehouse, sk["column_id"])


def test_the_system_type_is_for_system_columns_at_table_level_without_sql(warehouse, model):
    sk = system_column(warehouse, model["dim"]["id"])
    on_plain = put_column(
        warehouse, model["fact"]["id"], model["plain"]["id"], mapping_type="system"
    )
    with_sql = put_column(
        warehouse, model["dim"]["id"], sk["column_id"], mapping_type="system", sql_expression="1"
    )
    created = warehouse.client("editor").post(
        f"{mapping_path(warehouse, model['dim']['id'])}/branches",
        json={"name": "B", "driving_input": "stg_orders"},
    )
    branch_id = created.json()["branches"][0]["id"]
    in_branch = warehouse.client("editor").put(
        f"{mapping_path(warehouse, model['dim']['id'])}/branches/{branch_id}"
        f"/columns/{sk['column_id']}",
        json={"mapping_type": "system"},
    )

    assert on_plain.status_code == 422
    assert with_sql.status_code == 422
    assert in_branch.status_code == 422


def test_a_generated_table_needs_no_inputs_so_its_columns_are_covered(warehouse, model):
    engine = warehouse.app.state.engine
    table_id = uuid.uuid4()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into dw_tables (id, data_warehouse_id, layer, name, kind, is_aggregate,"
                " is_conformed, description, created_at, updated_at, version) values (:id, :wh,"
                " 'core', 'dim_date', 'generated', false, false, '', :now, :now, 1)"
            ),
            {"id": table_id, "wh": _warehouse_id(engine), "now": now},
        )
        connection.execute(
            sa.text(
                "insert into dw_columns (id, table_id, name, ordinal, data_type, is_nullable,"
                " role, description, is_system, created_at, updated_at, version) values"
                " (:id, :t, 'calendar_date', 1, '{\"type\": \"date\"}', false, 'attribute', '',"
                " false, :now, :now, 1)"
            ),
            {"id": uuid.uuid4(), "t": table_id, "now": now},
        )

    coverage = get_mapping(warehouse, str(table_id))["coverage"]

    assert coverage and all(c["covered"] for c in coverage)


def test_lookups_are_saved_by_editors_only(warehouse, model):
    assert lookup(warehouse, model, role="viewer").status_code == 403
