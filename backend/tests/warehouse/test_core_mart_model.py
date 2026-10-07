"""The Core and Mart model editor (spec §6.9, stories 89-93a)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from dawam.modules.audit import AuditService
from tests.roles import RoleClients

GRAIN = "One row per order line"


def base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def create(roles: RoleClients, role="editor", **body):
    return roles.client(role).post(f"{base(roles)}/tables", json=body)


def table(roles: RoleClients, **body) -> dict:
    response = create(roles, **body)
    assert response.status_code == 201, response.text
    return response.json()


def fact(roles: RoleClients, name="fact_sales", layer="core", **body) -> dict:
    return table(
        roles,
        **{
            "layer": layer,
            "name": name,
            "kind": "fact",
            "grain": GRAIN,
            "fact_type": "transactional",
        }
        | body,
    )


def dimension(roles: RoleClients, name="dim_customer", layer="core", **body) -> dict:
    return table(roles, layer=layer, name=name, kind="dimension", **body)


def add_column(roles: RoleClients, table_id: str, **body):
    return roles.client("editor").post(f"{base(roles)}/tables/{table_id}/columns", json=body)


def column(roles: RoleClients, table_id: str, **body) -> dict:
    response = add_column(roles, table_id, **body)
    assert response.status_code == 201, response.text
    return response.json()


def get_table(roles: RoleClients, table_id: str) -> dict:
    response = roles.client("viewer").get(f"{base(roles)}/tables/{table_id}")
    assert response.status_code == 200, response.text
    return response.json()


def names(detail: dict) -> list[str]:
    return [c["name"] for c in detail["columns"]]


def code_of(response) -> str:
    return response.json()["error"]["code"]


# --- facts ---------------------------------------------------------------------------


def test_a_fact_needs_a_grain_and_a_fact_type(warehouse: RoleClients):
    missing_grain = create(
        warehouse, layer="core", name="fact_a", kind="fact", fact_type="factless"
    )
    missing_type = create(warehouse, layer="core", name="fact_a", kind="fact", grain=GRAIN)
    blank = create(
        warehouse, layer="core", name="fact_a", kind="fact", grain="  ", fact_type="factless"
    )
    unknown = create(
        warehouse, layer="core", name="fact_a", kind="fact", grain=GRAIN, fact_type="weekly"
    )

    for response in (missing_grain, missing_type, blank, unknown):
        assert response.status_code == 422, response.text
    assert code_of(missing_grain) == "invalid_model"
    assert missing_grain.json()["error"]["details"]["field"] == "grain"
    assert missing_type.json()["error"]["details"]["field"] == "fact_type"


@pytest.mark.parametrize(
    "fact_type", ["transactional", "periodic_snapshot", "accumulating_snapshot", "factless"]
)
def test_a_fact_records_its_grain_and_type(warehouse: RoleClients, fact_type: str):
    created = fact(warehouse, fact_type=fact_type)

    assert created["kind"] == "fact"
    assert created["grain"] == GRAIN
    assert created["fact_type"] == fact_type
    assert created["scd_type"] is None
    assert created["unknown_member"] is None
    assert created["columns"] == []
    assert created["version"] == 1


def test_only_a_fact_has_a_grain_or_a_fact_type(warehouse: RoleClients):
    response = create(warehouse, layer="core", name="dim_a", kind="dimension", grain=GRAIN)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "grain"


def test_a_table_name_follows_the_platforms_rules_and_is_unique_in_its_layer(
    warehouse: RoleClients,
):
    fact(warehouse, name="fact_sales")

    duplicate = create(warehouse, layer="core", name="FACT_SALES", kind="dimension")
    other_layer = create(warehouse, layer="mart", name="fact_sales", kind="dimension")
    bad = create(warehouse, layer="core", name="1 bad name", kind="dimension")
    reserved = create(warehouse, layer="core", name="select", kind="dimension")

    assert duplicate.status_code == 409 and code_of(duplicate) == "name_taken"
    assert other_layer.status_code == 201
    assert bad.status_code == 422 and code_of(bad) == "invalid_model"
    assert reserved.status_code == 422


def test_core_and_mart_tables_only(warehouse: RoleClients):
    response = create(warehouse, layer="staging", name="stg_a", kind="dimension")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "layer"


def test_the_model_needs_the_data_warehouse_set_up(roles: RoleClients):
    response = create(roles, layer="core", name="dim_a", kind="dimension")

    assert response.status_code == 404
    assert code_of(response) == "not_set_up"
    listed = roles.client("viewer").get(f"{base(roles)}/tables")
    assert listed.status_code == 200 and listed.json()["items"] == []


# --- dimensions, SCD and the unknown member ------------------------------------------


def test_a_dimension_gets_a_surrogate_key_and_an_unknown_member(warehouse: RoleClients):
    created = dimension(warehouse)

    assert created["scd_type"] == 1
    assert created["is_conformed"] is False
    assert created["unknown_member"] == {"surrogate_key": -1, "defaults": {}}
    assert names(created) == ["dim_customer_key"]
    key = created["columns"][0]
    assert key["role"] == "sk" and key["is_nullable"] is False
    assert key["data_type"] == {"type": "bigint", "length": None, "precision": None, "scale": None}
    assert key["is_system"] is False


def test_a_dimension_scd_type_is_zero_one_or_two(warehouse: RoleClients):
    for scd in (0, 1, 2):
        assert dimension(warehouse, name=f"dim_{scd}", scd_type=scd)["scd_type"] == scd

    response = create(warehouse, layer="core", name="dim_x", kind="dimension", scd_type=3)
    assert response.status_code == 422


def test_making_a_dimension_scd2_adds_the_housekeeping_columns(warehouse: RoleClients):
    created = dimension(warehouse, scd_type=2)

    assert names(created) == [
        "dim_customer_key",
        "scd_valid_from",
        "scd_valid_to",
        "scd_current_flag",
        "row_hash",
    ]
    roles = {c["name"]: c["role"] for c in created["columns"]}
    assert roles["scd_valid_from"] == "scd_valid_from"
    assert roles["scd_valid_to"] == "scd_valid_to"
    assert roles["scd_current_flag"] == "scd_current_flag"
    assert roles["row_hash"] == "row_hash"
    assert all(c["is_system"] for c in created["columns"][1:])

    # Going SCD2 later adds them too, and leaving SCD2 takes them away again.
    other = dimension(warehouse, name="dim_product", scd_type=1)
    assert names(other) == ["dim_product_key"]
    changed = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{other['id']}", json={"version": 1, "scd_type": 2}
    )
    assert changed.status_code == 200, changed.text
    assert len(names(get_table(warehouse, other["id"]))) == 5
    back = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{other['id']}", json={"version": 2, "scd_type": 0}
    )
    assert back.status_code == 200
    assert names(get_table(warehouse, other["id"])) == ["dim_product_key"]


def test_an_scd2_attribute_override_adds_housekeeping_to_a_type_1_dimension(
    warehouse: RoleClients,
):
    created = dimension(warehouse, scd_type=1)
    segment = column(
        warehouse,
        created["id"],
        name="segment",
        data_type={"type": "string", "length": 40},
        role="attribute",
        scd_type_override=2,
    )

    assert segment["scd_type_override"] == 2
    assert "row_hash" in names(get_table(warehouse, created["id"]))

    cleared = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{created['id']}/columns/{segment['id']}",
        json={"version": 1, "scd_type_override": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert "row_hash" not in names(get_table(warehouse, created["id"]))


def test_an_scd_override_belongs_to_a_dimension_attribute(warehouse: RoleClients):
    sales = fact(warehouse)

    response = add_column(
        warehouse,
        sales["id"],
        name="note",
        data_type={"type": "string", "length": 40},
        role="attribute",
        scd_type_override=1,
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "scd_type_override"


def test_housekeeping_columns_are_not_edited_by_hand(warehouse: RoleClients):
    created = dimension(warehouse, scd_type=2)
    row_hash = next(c for c in created["columns"] if c["name"] == "row_hash")
    client = warehouse.client("editor")

    renamed = client.patch(
        f"{base(warehouse)}/tables/{created['id']}/columns/{row_hash['id']}",
        json={"version": 1, "name": "hash"},
    )
    deleted = client.delete(f"{base(warehouse)}/tables/{created['id']}/columns/{row_hash['id']}")
    forged = add_column(
        warehouse,
        created["id"],
        name="my_hash",
        data_type={"type": "string", "length": 64},
        role="row_hash",
    )

    for response in (renamed, deleted, forged):
        assert response.status_code == 422, response.text
    assert code_of(renamed) == "system_column"
    assert code_of(forged) == "invalid_model"


def test_the_unknown_member_defaults_are_configurable(warehouse: RoleClients):
    created = dimension(warehouse)
    column(
        warehouse,
        created["id"],
        name="customer_name",
        data_type={"type": "string", "length": 100},
        role="attribute",
    )

    response = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{created['id']}",
        json={"version": 1, "unknown_member_defaults": {"customer_name": "Unknown"}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["unknown_member"] == {
        "surrogate_key": -1,
        "defaults": {"customer_name": "Unknown"},
    }
    nothing = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{created['id']}",
        json={"version": 2, "unknown_member_defaults": {"no_such_column": "x"}},
    )
    assert nothing.status_code == 422
    assert nothing.json()["error"]["details"]["field"] == "unknown_member_defaults"


def test_only_a_dimension_has_an_unknown_member(warehouse: RoleClients):
    sales = fact(warehouse)

    response = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{sales['id']}",
        json={"version": 1, "unknown_member_defaults": {"a": "b"}},
    )

    assert response.status_code == 422


def test_renaming_or_deleting_a_column_keeps_the_unknown_member_defaults_valid(
    warehouse: RoleClients,
):
    created = dimension(warehouse)
    name = column(
        warehouse,
        created["id"],
        name="customer_name",
        data_type={"type": "string", "length": 100},
        role="attribute",
    )
    warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{created['id']}",
        json={"version": 1, "unknown_member_defaults": {"customer_name": "Unknown"}},
    )
    client = warehouse.client("editor")
    cols = f"{base(warehouse)}/tables/{created['id']}/columns/{name['id']}"

    client.patch(cols, json={"version": 1, "name": "full_name"})
    assert get_table(warehouse, created["id"])["unknown_member"]["defaults"] == {
        "full_name": "Unknown"
    }
    client.delete(cols)
    assert get_table(warehouse, created["id"])["unknown_member"]["defaults"] == {}


# --- columns -------------------------------------------------------------------------


def test_columns_store_neutral_data_types(warehouse: RoleClients):
    created = dimension(warehouse)

    amount = column(
        warehouse,
        created["id"],
        name="credit_limit",
        data_type={"type": "decimal", "precision": 18, "scale": 2},
        role="attribute",
        semantic_type="amount",
        description="Approved limit",
    )
    text = column(
        warehouse,
        created["id"],
        name="customer_name",
        data_type={"type": "string", "length": 100},
        role="attribute",
        is_nullable=False,
    )

    assert amount["data_type"] == {"type": "decimal", "length": None, "precision": 18, "scale": 2}
    assert amount["semantic_type"] == "amount" and amount["description"] == "Approved limit"
    assert amount["is_nullable"] is True and text["is_nullable"] is False
    assert text["data_type"]["length"] == 100
    assert [c["ordinal"] for c in get_table(warehouse, created["id"])["columns"]] == [1, 2, 3]


@pytest.mark.parametrize(
    "data_type",
    [
        {"type": "varchar2"},
        {"type": "decimal", "precision": 40, "scale": 2},
        {"type": "decimal", "precision": 10, "scale": 11},
        {"type": "decimal", "length": 10},
        {"type": "integer", "precision": 10},
        {"type": "string", "length": 0},
    ],
)
def test_an_invalid_data_type_is_refused(warehouse: RoleClients, data_type: dict):
    created = dimension(warehouse)

    response = add_column(warehouse, created["id"], name="x", data_type=data_type, role="attribute")

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["field"] == "data_type"


def test_a_column_name_is_unique_in_its_table_and_valid_on_the_platform(
    warehouse: RoleClients,
):
    created = dimension(warehouse)
    column(
        warehouse,
        created["id"],
        name="customer_name",
        data_type={"type": "string", "length": 10},
        role="attribute",
    )

    duplicate = add_column(
        warehouse,
        created["id"],
        name="CUSTOMER_NAME",
        data_type={"type": "string", "length": 10},
        role="attribute",
    )
    bad = add_column(
        warehouse,
        created["id"],
        name="has space",
        data_type={"type": "string", "length": 10},
        role="attribute",
    )

    assert duplicate.status_code == 409 and code_of(duplicate) == "name_taken"
    assert bad.status_code == 422


def test_measures_belong_to_facts_and_declare_additivity(warehouse: RoleClients):
    sales = fact(warehouse)
    customer = dimension(warehouse)

    amount = column(
        warehouse,
        sales["id"],
        name="amount",
        data_type={"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
        additivity="additive",
        semantic_type="amount",
    )
    balance = column(
        warehouse,
        sales["id"],
        name="balance",
        data_type={"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
        additivity="semi_additive",
    )
    rate = column(
        warehouse,
        sales["id"],
        name="rate",
        data_type={"type": "decimal", "precision": 9, "scale": 4},
        role="measure",
        additivity="non_additive",
    )
    in_dimension = add_column(
        warehouse,
        customer["id"],
        name="amount",
        data_type={"type": "integer"},
        role="measure",
        additivity="additive",
    )
    on_attribute = add_column(
        warehouse,
        sales["id"],
        name="channel",
        data_type={"type": "string", "length": 10},
        role="attribute",
        additivity="additive",
    )
    unknown = add_column(
        warehouse,
        sales["id"],
        name="qty",
        data_type={"type": "integer"},
        role="measure",
        additivity="mostly",
    )

    assert [m["additivity"] for m in (amount, balance, rate)] == [
        "additive",
        "semi_additive",
        "non_additive",
    ]
    for response in (in_dimension, on_attribute, unknown):
        assert response.status_code == 422, response.text
    assert on_attribute.json()["error"]["details"]["field"] == "additivity"


def test_degenerate_dimensions_and_audit_columns(warehouse: RoleClients):
    sales = fact(warehouse)
    customer = dimension(warehouse)

    invoice = column(
        warehouse,
        sales["id"],
        name="invoice_number",
        data_type={"type": "string", "length": 20},
        role="degenerate_dimension",
    )
    loaded = column(
        warehouse,
        sales["id"],
        name="etl_loaded_at",
        data_type={"type": "timestamp"},
        role="audit",
    )
    misplaced = add_column(
        warehouse,
        customer["id"],
        name="invoice_number",
        data_type={"type": "string", "length": 20},
        role="degenerate_dimension",
    )

    assert invoice["role"] == "degenerate_dimension" and loaded["role"] == "audit"
    assert misplaced.status_code == 422


# --- foreign keys --------------------------------------------------------------------


def test_a_fact_fk_references_a_dimension_with_an_optional_role_name(warehouse: RoleClients):
    sales = fact(warehouse)
    dates = dimension(warehouse, name="dim_date")

    order_date = column(
        warehouse,
        sales["id"],
        name="order_date_key",
        data_type={"type": "integer"},
        role="fk",
        references_table_id=dates["id"],
        role_name="order_date",
    )
    ship_date = column(
        warehouse,
        sales["id"],
        name="ship_date_key",
        data_type={"type": "integer"},
        role="fk",
        references_table_id=dates["id"],
        role_name="ship_date",
    )

    assert order_date["references_table_id"] == dates["id"]
    assert order_date["role_name"] == "order_date"
    assert ship_date["role_name"] == "ship_date"

    twice = add_column(
        warehouse,
        sales["id"],
        name="due_date_key",
        data_type={"type": "integer"},
        role="fk",
        references_table_id=dates["id"],
        role_name="order_date",
    )
    assert twice.status_code == 422 and code_of(twice) == "invalid_model"
    assert twice.json()["error"]["details"]["field"] == "role_name"


def test_a_foreign_key_is_valid_only_with_a_dimension_to_point_at(warehouse: RoleClients):
    sales = fact(warehouse)
    other_fact = fact(warehouse, name="fact_returns")
    dates = dimension(warehouse, name="dim_date")
    body = {"data_type": {"type": "integer"}, "role": "fk"}

    no_target = add_column(warehouse, sales["id"], name="a_key", **body)
    to_fact = add_column(
        warehouse, sales["id"], name="b_key", references_table_id=other_fact["id"], **body
    )
    missing = add_column(
        warehouse, sales["id"], name="c_key", references_table_id=str(uuid.uuid4()), **body
    )
    on_attribute = add_column(
        warehouse,
        sales["id"],
        name="d_key",
        data_type={"type": "integer"},
        role="attribute",
        references_table_id=dates["id"],
    )
    role_name_without_fk = add_column(
        warehouse,
        sales["id"],
        name="e_key",
        data_type={"type": "integer"},
        role="attribute",
        role_name="x",
    )
    to_itself = add_column(
        warehouse, dates["id"], name="f_key", references_table_id=dates["id"], **body
    )

    for response in (no_target, to_fact, missing, on_attribute, role_name_without_fk, to_itself):
        assert response.status_code == 422, response.text
    assert no_target.json()["error"]["details"]["field"] == "references_table_id"


def test_core_facts_use_core_dimensions_and_mart_facts_may_use_core_conformed_ones(
    warehouse: RoleClients,
):
    core_private = dimension(warehouse, name="dim_private")
    core_conformed = dimension(warehouse, name="dim_customer", is_conformed=True)
    mart_dim = dimension(warehouse, name="dim_region", layer="mart")
    mart_fact = fact(warehouse, name="fact_sales_monthly", layer="mart", is_aggregate=True)
    core_fact = fact(warehouse)
    body = {"data_type": {"type": "bigint"}, "role": "fk"}

    conformed_ok = column(
        warehouse,
        mart_fact["id"],
        name="customer_key",
        references_table_id=core_conformed["id"],
        **body,
    )
    own_layer_ok = column(
        warehouse, mart_fact["id"], name="region_key", references_table_id=mart_dim["id"], **body
    )
    not_conformed = add_column(
        warehouse,
        mart_fact["id"],
        name="private_key",
        references_table_id=core_private["id"],
        **body,
    )
    mart_from_core = add_column(
        warehouse, core_fact["id"], name="region_key", references_table_id=mart_dim["id"], **body
    )

    assert conformed_ok["references_table_id"] == core_conformed["id"]
    assert own_layer_ok["references_table_id"] == mart_dim["id"]
    assert not_conformed.status_code == 422
    assert not_conformed.json()["error"]["details"]["field"] == "references_table_id"
    assert mart_from_core.status_code == 422
    assert get_table(warehouse, mart_fact["id"])["is_aggregate"] is True


def test_a_dimension_is_marked_conformed_and_reused_across_facts(warehouse: RoleClients):
    customer = dimension(warehouse, name="dim_customer")
    assert customer["is_conformed"] is False

    changed = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{customer['id']}", json={"version": 1, "is_conformed": True}
    )
    assert changed.status_code == 200 and changed.json()["is_conformed"] is True

    for name in ("fact_sales", "fact_returns"):
        sales = fact(warehouse, name=name)
        column(
            warehouse,
            sales["id"],
            name="customer_key",
            data_type={"type": "bigint"},
            role="fk",
            references_table_id=customer["id"],
        )
    fact_only = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{fact(warehouse, name='fact_x')['id']}",
        json={"version": 1, "is_conformed": True},
    )
    assert fact_only.status_code == 422


def test_a_referenced_dimension_cannot_be_deleted_until_nothing_points_at_it(
    warehouse: RoleClients,
):
    sales = fact(warehouse)
    customer = dimension(warehouse)
    fk = column(
        warehouse,
        sales["id"],
        name="customer_key",
        data_type={"type": "bigint"},
        role="fk",
        references_table_id=customer["id"],
    )
    client = warehouse.client("editor")

    blocked = client.delete(f"{base(warehouse)}/tables/{customer['id']}")
    assert blocked.status_code == 409 and code_of(blocked) == "table_referenced"

    client.delete(f"{base(warehouse)}/tables/{sales['id']}/columns/{fk['id']}")
    assert client.delete(f"{base(warehouse)}/tables/{customer['id']}").status_code == 204
    assert client.get(f"{base(warehouse)}/tables/{customer['id']}").status_code == 404


def test_a_bridge_table_has_a_group_key_and_two_foreign_keys(warehouse: RoleClients):
    accounts = dimension(warehouse, name="dim_account")
    customers = dimension(warehouse, name="dim_customer")

    bridge = table(warehouse, layer="core", name="bridge_account_customer", kind="bridge")

    assert bridge["kind"] == "bridge" and bridge["scd_type"] is None
    assert names(bridge) == ["bridge_account_customer_group_key"]
    assert bridge["columns"][0]["role"] == "sk"
    for target in (accounts, customers):
        column(
            warehouse,
            bridge["id"],
            name=f"{target['name']}_key",
            data_type={"type": "bigint"},
            role="fk",
            references_table_id=target["id"],
        )
    assert len(get_table(warehouse, bridge["id"])["columns"]) == 3


# --- editing, listing, concurrency, audit ----------------------------------------------


def test_the_layers_tables_are_listed(warehouse: RoleClients):
    fact(warehouse)
    dimension(warehouse)
    dimension(warehouse, name="dim_region", layer="mart")

    core = warehouse.client("viewer").get(f"{base(warehouse)}/tables", params={"layer": "core"})
    everything = warehouse.client("viewer").get(f"{base(warehouse)}/tables")

    assert core.status_code == 200
    assert [t["name"] for t in core.json()["items"]] == ["dim_customer", "fact_sales"]
    assert core.json()["items"][1]["column_count"] == 0
    assert core.json()["items"][0]["column_count"] == 1
    assert len(everything.json()["items"]) == 3


def test_a_table_is_edited_under_optimistic_concurrency(warehouse: RoleClients):
    sales = fact(warehouse)
    client = warehouse.client("editor")
    url = f"{base(warehouse)}/tables/{sales['id']}"

    edited = client.patch(
        url, json={"version": 1, "name": "fact_orders", "grain": "One row per order"}
    )
    stale = client.patch(url, json={"version": 1, "description": "late"})

    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "fact_orders" and edited.json()["version"] == 2
    assert edited.json()["grain"] == "One row per order"
    assert stale.status_code == 409 and code_of(stale) == "version_conflict"
    assert stale.json()["error"]["details"] == {"current_version": 2}
    blank_grain = client.patch(url, json={"version": 2, "grain": ""})
    assert blank_grain.status_code == 422


def test_a_column_is_edited_under_optimistic_concurrency(warehouse: RoleClients):
    sales = fact(warehouse)
    amount = column(
        warehouse,
        sales["id"],
        name="amount",
        data_type={"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
    )
    client = warehouse.client("editor")
    url = f"{base(warehouse)}/tables/{sales['id']}/columns/{amount['id']}"

    edited = client.patch(
        url, json={"version": 1, "additivity": "additive", "description": "Net amount"}
    )
    stale = client.patch(url, json={"version": 1, "description": "late"})

    assert edited.status_code == 200, edited.text
    assert edited.json()["additivity"] == "additive" and edited.json()["version"] == 2
    assert stale.status_code == 409 and code_of(stale) == "version_conflict"
    # Additivity belongs to a measure: it must go before the role can change.
    as_attribute = client.patch(url, json={"version": 2, "role": "attribute"})
    assert as_attribute.status_code == 422


def test_an_unknown_table_or_column_is_404(warehouse: RoleClients):
    sales = fact(warehouse)
    client = warehouse.client("editor")
    missing = uuid.uuid4()

    assert client.get(f"{base(warehouse)}/tables/{missing}").status_code == 404
    assert (
        client.patch(
            f"{base(warehouse)}/tables/{sales['id']}/columns/{missing}", json={"version": 1}
        ).status_code
        == 404
    )
    assert client.delete(f"{base(warehouse)}/tables/{missing}").status_code == 404


def test_changes_are_audited_and_visible_in_the_activity_feed(warehouse: RoleClients):
    sales = fact(warehouse)
    amount = column(
        warehouse,
        sales["id"],
        name="amount",
        data_type={"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
        additivity="additive",
    )
    client = warehouse.client("editor")
    clock = warehouse.app.state.services.clock
    clock.advance(timedelta(seconds=1))  # the trail is ordered by time
    client.patch(
        f"{base(warehouse)}/tables/{sales['id']}", json={"version": 1, "description": "Sales"}
    )
    clock.advance(timedelta(seconds=1))
    client.patch(
        f"{base(warehouse)}/tables/{sales['id']}/columns/{amount['id']}",
        json={"version": 1, "additivity": "semi_additive"},
    )

    audit = AuditService(warehouse.app.state.engine)
    table_trail = audit.list(warehouse.workspace_id, entity_type="dw_table", entity_id=sales["id"])
    column_trail = audit.list(
        warehouse.workspace_id, entity_type="dw_column", entity_id=amount["id"]
    )

    assert [e.old is None for e in table_trail] == [True, False]
    assert table_trail[0].new["name"] == "fact_sales"
    assert table_trail[1].old == {"description": ""} and table_trail[1].new == {
        "description": "Sales"
    }
    assert column_trail[0].new["name"] == "amount"
    assert column_trail[1].old == {"additivity": "additive"}
    assert column_trail[1].new == {"additivity": "semi_additive"}

    clock.advance(timedelta(seconds=1))
    client.delete(f"{base(warehouse)}/tables/{sales['id']}")
    trail = audit.list(warehouse.workspace_id, entity_type="dw_table", entity_id=sales["id"])
    assert trail[-1].new is None and trail[-1].old["name"] == "fact_sales"

    feed = warehouse.client("viewer").get(f"/api/v1/workspaces/{warehouse.workspace_id}/activity")
    verbs = [item["verb"] for item in feed.json()["items"]]
    assert "dw_table.created" in verbs and "dw_table.deleted" in verbs


def test_a_viewer_reads_the_model_but_cannot_change_it(warehouse: RoleClients):
    sales = fact(warehouse)

    forbidden = create(warehouse, role="viewer", layer="core", name="dim_a", kind="dimension")
    stranger = warehouse.client("non_member").get(f"{base(warehouse)}/tables/{sales['id']}")

    assert forbidden.status_code == 403
    assert stranger.status_code == 404
    assert (
        warehouse.client("viewer").get(f"{base(warehouse)}/tables/{sales['id']}").status_code == 200
    )


def test_an_archived_workspace_refuses_model_changes(warehouse: RoleClients):
    sales = fact(warehouse)
    viewer = warehouse.client("viewer")  # members are added while the Workspace is active
    archived = warehouse.client("owner").post(
        f"/api/v1/workspaces/{warehouse.workspace_id}/archive"
    )
    assert archived.status_code == 204, archived.text

    refused = create(warehouse, layer="core", name="dim_a", kind="dimension")

    assert refused.status_code == 409 and code_of(refused) == "workspace_archived"
    assert viewer.get(f"{base(warehouse)}/tables/{sales['id']}").status_code == 200


def test_model_tables_are_stored_per_data_warehouse(warehouse: RoleClients):
    created = fact(warehouse)
    engine = warehouse.app.state.engine

    with engine.connect() as connection:
        row = connection.execute(
            sa.text("select layer, kind, version from dw_tables where id = :id"),
            {"id": created["id"]},
        ).one()

    assert tuple(row) == ("core", "fact", 1)
