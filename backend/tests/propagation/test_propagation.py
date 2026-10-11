"""Change propagation (spec §6.10, stories 53, 113), through the HTTP API.

Each row of the propagation rule table gets a fixture DW Schema and the impact list and
Change Set it must produce: rename, type change, nullability, delete / ``source_removed``,
new input. Core and Mart are built with the model editor; Staging comes from a seeded
Source Schema.
"""

from __future__ import annotations

import pytest

from tests.kpis.test_kpis import kpis_path
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import add_system, columns_of, generate, seed
from tests.warehouse.test_staging_generation import staging_tables as staging


def dw(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def change_sets(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/change-sets"


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def table(roles: RoleClients, name: str, layer: str, kind="dimension") -> dict:
    body = {"layer": layer, "name": name, "kind": kind}
    if kind == "fact":
        body |= {"grain": "One row per sale", "fact_type": "transactional"}
    response = roles.client("editor").post(f"{dw(roles)}/tables", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def column(roles: RoleClients, table_id: str, name: str, data_type=None, **extra) -> dict:
    body = {"name": name, "data_type": data_type or {"type": "string", "length": 40}}
    body |= {"role": "attribute"} | extra
    response = roles.client("editor").post(f"{dw(roles)}/tables/{table_id}/columns", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def map_column(roles: RoleClients, target: dict, sql: str, mapping_type="direct") -> dict:
    response = roles.client("editor").put(
        f"{dw(roles)}/tables/{target['table_id']}/mapping/columns/{target['id']}",
        json={"version": 0, "mapping_type": mapping_type, "sql_expression": sql},
    )
    assert response.status_code == 200, response.text
    return response.json()


def edit_column(roles: RoleClients, target: dict, **changes):
    current = get_column(roles, target)
    return roles.client("editor").patch(
        f"{dw(roles)}/tables/{target['table_id']}/columns/{target['id']}",
        json={"version": current["version"], **changes},
    )


def get_column(roles: RoleClients, target: dict) -> dict:
    response = roles.client("viewer").get(f"{dw(roles)}/tables/{target['table_id']}")
    assert response.status_code == 200, response.text
    return next(c for c in response.json()["columns"] if c["id"] == target["id"])


def mapping_sql(roles: RoleClients, target: dict) -> tuple[str, str]:
    response = roles.client("viewer").get(f"{dw(roles)}/tables/{target['table_id']}/mapping")
    assert response.status_code == 200, response.text
    found = next(c for c in response.json()["columns"] if c["column_id"] == target["id"])
    return found["mapping_type"], found["sql_expression"]


def kpi(roles: RoleClients, name: str, sql: str) -> dict:
    response = roles.client("editor").post(
        kpis_path(roles), json={"name": name, "formula_sql": sql}
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_kpi(roles: RoleClients, found: dict) -> dict:
    response = roles.client("viewer").get(f"{kpis_path(roles)}/{found['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def propagations(roles: RoleClients) -> list[dict]:
    response = roles.client("viewer").get(change_sets(roles))
    assert response.status_code == 200, response.text
    return [c for c in response.json()["items"] if c["origin"] == "propagation"]


def only_propagation(roles: RoleClients) -> dict:
    [found] = [c for c in propagations(roles) if c["status"] == "pending"]
    response = roles.client("viewer").get(f"{change_sets(roles)}/{found['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def accept(roles: RoleClients, change_set_id: str, item_ids: list[str] | None = None) -> dict:
    body = {} if item_ids is None else {"item_ids": item_ids}
    response = roles.client("editor").post(
        f"{change_sets(roles)}/{change_set_id}/accept", json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def reject(roles: RoleClients, change_set_id: str) -> None:
    response = roles.client("editor").post(f"{change_sets(roles)}/{change_set_id}/reject", json={})
    assert response.status_code == 200, response.text


def alerts(roles: RoleClients, role="editor") -> list[dict]:
    unread = roles.client(role).get("/api/v1/notifications").json()["items"]
    return [n for n in unread if n["kind"] == "impact_alert"]


def problems(roles: RoleClients, code: str) -> list[dict]:
    response = roles.client("viewer").get(f"{dw(roles)}/validation")
    assert response.status_code == 200, response.text
    return [p for p in response.json()["problems"] if p["code"] == code]


def labelled(detail: dict) -> dict[str, dict]:
    return {i["label"]: i for i in detail["items"]}


@pytest.fixture
def model(warehouse: RoleClients) -> dict:
    """Core ``dim_customer`` (customer_name, email, segment) feeding Mart ``dim_customer_m``
    (customer_name and segment mapped direct, email_domain derived from email), and a KPI
    over the Mart."""
    core = table(warehouse, "dim_customer", "core")
    mart = table(warehouse, "dim_customer_m", "mart")
    found = {
        "core": core,
        "mart": mart,
        "name": column(warehouse, core["id"], "customer_name"),
        "email": column(warehouse, core["id"], "email", is_nullable=False),
        "segment": column(warehouse, core["id"], "segment", {"type": "string", "length": 10}),
        "name_m": column(warehouse, mart["id"], "customer_name"),
        "domain_m": column(warehouse, mart["id"], "email_domain", is_nullable=False),
        "segment_m": column(warehouse, mart["id"], "segment", {"type": "string", "length": 10}),
    }
    map_column(warehouse, found["name_m"], "dim_customer.customer_name")
    map_column(warehouse, found["domain_m"], "SPLIT_PART(dim_customer.email, '@', 2)", "derived")
    map_column(warehouse, found["segment_m"], "dim_customer.segment")
    found["kpi"] = kpi(
        warehouse,
        "Customers",
        "SELECT COUNT(DISTINCT m.customer_name) FROM dim_customer_m AS m",
    )
    return found


# -- rename -------------------------------------------------------------------------------


def test_a_rename_rewrites_mappings_and_offers_the_same_named_downstream_rename(
    model, warehouse: RoleClients
):
    response = edit_column(warehouse, model["name"], name="full_name")
    assert response.status_code == 200, response.text

    detail = only_propagation(warehouse)
    items = labelled(detail)
    assert list(items) == [
        "[Mart] mart.dim_customer_m.customer_name: rename to full_name (optional)",
        "[Mart] mart.dim_customer_m.customer_name mapping: follow the rename",
        "[KPIs] KPI Customers: follow the rename",
    ]
    rename, mapping, formula = detail["items"]
    assert mapping["payload"] == {"sql_expression": "dim_customer.full_name"}
    assert mapping["depends_on"] == []
    # The KPI reads the Mart column, so its rewrite follows the optional rename.
    assert formula["depends_on"] == [rename["id"]]
    assert formula["payload"]["formula_sql"] == (
        "SELECT COUNT(DISTINCT m.full_name) FROM dim_customer_m AS m"
    )
    assert detail["change_set"]["origin"] == "propagation"

    accept(warehouse, detail["change_set"]["id"])

    assert mapping_sql(warehouse, model["name_m"]) == ("direct", "dim_customer.full_name")
    assert get_column(warehouse, model["name_m"])["name"] == "full_name"
    assert get_kpi(warehouse, model["kpi"])["formula_sql"] == (
        "SELECT COUNT(DISTINCT m.full_name) FROM dim_customer_m AS m"
    )
    assert problems(warehouse, "broken_reference") == []
    # Applying a propagation never propagates again.
    assert [c["status"] for c in propagations(warehouse)] == ["applied"]


def test_a_rename_raises_an_impact_alert_naming_the_impacted_assets(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")

    detail = only_propagation(warehouse)
    for role, expected in (("editor", 1), ("owner", 1), ("viewer", 0)):
        assert len(alerts(warehouse, role)) == expected, role
    [alert] = alerts(warehouse)
    assert alert["ref_id"] == detail["change_set"]["id"]
    assert "customer_name renamed to full_name" in alert["message"]
    assert "Mart: dim_customer_m.customer_name" in alert["message"]
    assert "KPIs: Customers" in alert["message"]


def test_accepting_in_part_leaves_the_rest_as_validation_errors(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")
    detail = only_propagation(warehouse)
    mapping = detail["items"][1]

    # Keep the Mart column's name: only the mapping follows.
    accept(warehouse, detail["change_set"]["id"], [mapping["id"]])

    assert get_column(warehouse, model["name_m"])["name"] == "customer_name"
    assert mapping_sql(warehouse, model["name_m"]) == ("direct", "dim_customer.full_name")
    statuses = {i["label"]: i["status"] for i in only_closed(warehouse, detail)["items"]}
    assert list(statuses.values()) == ["expired", "accepted", "expired"]
    assert problems(warehouse, "broken_reference") == []


def only_closed(roles: RoleClients, detail: dict) -> dict:
    response = roles.client("viewer").get(f"{change_sets(roles)}/{detail['change_set']['id']}")
    return response.json()


def test_a_rejected_propagation_stays_visible_as_a_validation_error(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")

    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])

    [problem] = problems(warehouse, "broken_reference")
    assert (problem["table_name"], problem["column_name"]) == ("dim_customer_m", "customer_name")
    assert "dim_customer.customer_name" in problem["message"]


def test_a_table_rename_rewrites_mappings_branches_and_kpi_formulas(model, warehouse: RoleClients):
    branch = warehouse.client("editor").post(
        f"{dw(warehouse)}/tables/{model['mart']['id']}/mapping/branches",
        json={
            "name": "All",
            "driving_input": "dim_customer AS c",
            "filters": "c.segment <> 'x' AND dim_customer.email IS NOT NULL",
        },
    )
    assert branch.status_code == 201, branch.text
    over_core = kpi(warehouse, "Segments", "SELECT COUNT(*) FROM dim_customer")
    current = warehouse.client("viewer").get(f"{dw(warehouse)}/tables/{model['core']['id']}")

    response = warehouse.client("editor").patch(
        f"{dw(warehouse)}/tables/{model['core']['id']}",
        json={"version": current.json()["version"], "name": "dim_client"},
    )
    assert response.status_code == 200, response.text

    detail = only_propagation(warehouse)
    by_type: dict[str, list[dict]] = {}
    for item in detail["items"]:
        by_type.setdefault(item["object_type"], []).append(item)
    assert len(by_type["column_mapping"]) == 3
    [branch_item] = by_type["mapping_branch"]
    assert branch_item["payload"] == {
        "driving_input": "dim_client AS c",
        "filters": "c.segment <> 'x' AND dim_client.email IS NOT NULL",
    }
    [kpi_item] = by_type["kpi_formula"]
    assert kpi_item["object_id"] == over_core["id"]
    assert "dw_column" not in by_type  # tables are never renamed downstream

    accept(warehouse, detail["change_set"]["id"])

    assert mapping_sql(warehouse, model["domain_m"]) == (
        "derived",
        "SPLIT_PART(dim_client.email, '@', 2)",
    )
    assert get_kpi(warehouse, over_core)["formula_sql"] == "SELECT COUNT(*) FROM dim_client"
    assert problems(warehouse, "broken_reference") == []


# -- type change --------------------------------------------------------------------------


def test_a_type_change_offers_to_retype_direct_mapped_columns_only(model, warehouse):
    wider = {"type": "string", "length": 50}
    edit_column(warehouse, model["segment"], data_type=wider)
    edit_column(warehouse, model["email"], data_type={"type": "text"})

    sets = [c for c in propagations(warehouse) if c["status"] == "pending"]
    assert len(sets) == 1  # the email change reaches only a derived column: named, not retyped
    detail = only_propagation(warehouse)
    [item] = detail["items"]
    assert item["label"] == "[Mart] mart.dim_customer_m.segment: retype to string(50) (optional)"
    assert item["object_type"] == "dw_column"
    assert any("Mart: dim_customer_m.email_domain" in a["message"] for a in alerts(warehouse))

    accept(warehouse, detail["change_set"]["id"])

    assert get_column(warehouse, model["segment_m"])["data_type"]["length"] == 50


# -- nullability --------------------------------------------------------------------------


def test_a_now_nullable_input_flags_required_columns_it_feeds(model, warehouse: RoleClients):
    response = edit_column(warehouse, model["email"], is_nullable=True)
    assert response.status_code == 200, response.text

    assert propagations(warehouse) == []  # nothing to propose: flagged only
    [alert] = alerts(warehouse)
    assert "dim_customer.email now nullable" in alert["message"]
    assert "Mart: dim_customer_m.email_domain" in alert["message"]
    assert alert["ref_id"] is None


# -- delete / source_removed --------------------------------------------------------------


def test_a_delete_never_cascades_and_offers_to_unmap_what_read_it(model, warehouse):
    response = warehouse.client("editor").delete(
        f"{dw(warehouse)}/tables/{model['core']['id']}/columns/{model['email']['id']}"
    )
    assert response.status_code == 204, response.text

    detail = only_propagation(warehouse)
    [item] = detail["items"]
    assert item["label"] == (
        "[Mart] mart.dim_customer_m.email_domain mapping: unmap (reads a removed input)"
    )
    assert item["payload"] == {"mapping_type": "unmapped", "sql_expression": "", "lookup": None}
    # Nothing was deleted downstream, and until the item is accepted the mapping is an error.
    assert get_column(warehouse, model["domain_m"])["name"] == "email_domain"
    [problem] = problems(warehouse, "broken_reference")
    assert problem["column_name"] == "email_domain"
    assert "email removed" in alerts(warehouse)[0]["message"]

    accept(warehouse, detail["change_set"]["id"])

    assert mapping_sql(warehouse, model["domain_m"]) == ("unmapped", "")
    assert problems(warehouse, "broken_reference") == []
    unmapped = problems(warehouse, "unmapped_column")
    assert [p["column_name"] for p in unmapped if p["layer"] == "mart"] == ["email_domain"]


# -- new input and text edits -------------------------------------------------------------


def test_a_new_input_or_a_description_edit_raises_nothing(model, warehouse: RoleClients):
    column(warehouse, model["core"]["id"], "phone")
    edit_column(warehouse, model["name"], description="The customer's name")

    assert propagations(warehouse) == []
    assert alerts(warehouse) == []


def test_a_rename_nothing_reads_raises_nothing(model, warehouse: RoleClients):
    lonely = column(warehouse, model["core"]["id"], "nickname")

    edit_column(warehouse, lonely, name="alias_name")

    assert propagations(warehouse) == []
    assert alerts(warehouse) == []


# -- KPI re-pointing ----------------------------------------------------------------------


def test_a_measure_appearing_in_the_mart_offers_to_re_point_its_kpis(warehouse):
    fact = table(warehouse, "fact_sales", "core", "fact")
    amount = column(
        warehouse,
        fact["id"],
        "amount",
        {"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
        additivity="additive",
    )
    revenue = kpi(warehouse, "Revenue", "SELECT SUM(fact_sales.amount) FROM fact_sales")
    linked = warehouse.client("editor").put(
        f"{kpis_path(warehouse)}/{revenue['id']}/links",
        json={"version": revenue["version"], "dw_column_ids": [amount["id"]]},
    )
    assert linked.status_code == 200, linked.text
    mart = table(warehouse, "fact_sales_m", "mart", "fact")
    amount_m = column(
        warehouse,
        mart["id"],
        "amount",
        {"type": "decimal", "precision": 18, "scale": 2},
        role="measure",
        additivity="additive",
    )

    map_column(warehouse, amount_m, "fact_sales.amount")

    detail = only_propagation(warehouse)
    [item] = detail["items"]
    assert item["label"] == "[KPIs] KPI Revenue: re-point to the Mart"
    assert item["payload"] == {
        "links": [amount_m["id"]],
        "formula_sql": "SELECT SUM(fact_sales_m.amount) FROM fact_sales_m",
    }

    accept(warehouse, detail["change_set"]["id"])

    links = warehouse.client("viewer").get(f"{kpis_path(warehouse)}/{revenue['id']}/links")
    assert [c["dw_column_id"] for c in links.json()["items"]] == [amount_m["id"]]
    assert get_kpi(warehouse, revenue)["formula_sql"] == (
        "SELECT SUM(fact_sales_m.amount) FROM fact_sales_m"
    )


# -- through every Layer, and after a Snapshot ---------------------------------------------


@pytest.fixture
def layered(warehouse: RoleClients) -> dict:
    """Staging ``stg_crm_public_customers`` (id, name) feeding Core ``dim_customer.name``
    feeding Mart ``dim_customer_m.name``, and a KPI over the Mart."""
    system = add_system(warehouse)
    ids = seed(
        warehouse, system, "public", {"customers": [("id", "integer"), ("name", "varchar(40)")]}
    )
    assert generate(warehouse).status_code == 200
    stg = staging(warehouse)["stg_crm_public_customers"]
    core = table(warehouse, "dim_customer", "core")
    mart = table(warehouse, "dim_customer_m", "mart")
    name = column(warehouse, core["id"], "name")
    name_m = column(warehouse, mart["id"], "name")
    map_column(warehouse, name, "stg_crm_public_customers.name")
    map_column(warehouse, name_m, "dim_customer.name")
    kpi(warehouse, "Names", "SELECT COUNT(DISTINCT name) FROM dim_customer_m")
    return {
        "system": system,
        "ids": ids,
        "stg": stg,
        "stg_name": columns_of(warehouse, stg)["name"] | {"table_id": stg["id"]},
        "name": name,
        "name_m": name_m,
    }


def test_a_rename_travels_transitively_through_core_mart_and_kpis(layered, warehouse):
    response = edit_column(warehouse, layered["stg_name"], name="customer_name")
    assert response.status_code == 200, response.text

    detail = only_propagation(warehouse)
    items = detail["items"]
    assert [i["label"] for i in items] == [
        "[Core] core.dim_customer.name: rename to customer_name (optional)",
        "[Core] core.dim_customer.name mapping: follow the rename",
        "[Mart] mart.dim_customer_m.name: rename to customer_name (optional)",
        "[Mart] mart.dim_customer_m.name mapping: follow the rename",
        "[KPIs] KPI Names: follow the rename",
    ]
    core_rename, _, mart_rename, mart_mapping, formula = items
    assert mart_rename["depends_on"] == [core_rename["id"]]
    assert mart_mapping["depends_on"] == [core_rename["id"]]
    assert formula["depends_on"] == [mart_rename["id"]]

    # Accepting the KPI's item accepts what it depends on, transitively.
    accept(warehouse, detail["change_set"]["id"], [mart_mapping["id"], formula["id"]])

    assert get_column(warehouse, layered["name"])["name"] == "customer_name"
    assert get_column(warehouse, layered["name_m"])["name"] == "customer_name"
    assert mapping_sql(warehouse, layered["name_m"]) == ("direct", "dim_customer.customer_name")
    # The Core mapping's item was not accepted: it still reads the old Staging name.
    assert mapping_sql(warehouse, layered["name"]) == ("direct", "stg_crm_public_customers.name")
    assert [p["table_name"] for p in problems(warehouse, "broken_reference")] == ["dim_customer"]


def test_a_new_snapshot_is_synced_first_then_propagated(layered, warehouse: RoleClients):
    from tests.warehouse.test_staging_sync import run, sync

    run(
        warehouse,
        "update src_columns set current_definition = cast(:d as json) where id = :id",
        d='{"data_type": "varchar(80)", "is_nullable": true}',
        id=layered["ids"]["customers"]["columns"]["name"],
    )
    synced = sync(warehouse, layered["system"])
    assert propagations(warehouse) == []  # nothing propagates before the sync is applied

    accept(warehouse, synced["change_set_id"])

    detail = only_propagation(warehouse)
    assert detail["change_set"]["scope"] == {
        "trigger": "sync",
        "source_system_id": str(layered["system"]),
    }
    assert [i["label"] for i in detail["items"]] == [
        "[Core] core.dim_customer.name: retype to string(80) (optional)",
        "[Mart] mart.dim_customer_m.name: retype to string(80) (optional)",
    ]
    assert detail["items"][1]["depends_on"] == [detail["items"][0]["id"]]


def test_a_source_removed_input_is_unmapped_never_deleted(layered, warehouse: RoleClients):
    from tests.warehouse.test_staging_sync import run, sync

    run(
        warehouse,
        "update src_columns set status = 'source_removed' where id = :id",
        id=layered["ids"]["customers"]["columns"]["name"],
    )
    accept(warehouse, sync(warehouse, layered["system"])["change_set_id"])

    [problem] = problems(warehouse, "removed_input")
    assert problem["column_name"] == "name" and problem["layer"] == "core"
    detail = only_propagation(warehouse)
    assert [i["label"] for i in detail["items"]] == [
        "[Core] core.dim_customer.name mapping: unmap (reads a removed input)"
    ]
    accept(warehouse, detail["change_set"]["id"])

    assert mapping_sql(warehouse, layered["name"]) == ("unmapped", "")
    assert columns_of(warehouse, layered["stg"])["name"]["status"] == "source_removed"
    assert problems(warehouse, "removed_input") == []
