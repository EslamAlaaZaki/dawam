""" "Fix impacts" (spec §6.10, story 113a), through the HTTP API.

It recomputes the propagation Change Set from the open validation errors propagation can
fix: a mapping reading a renamed, deleted or ``source_removed`` input. Expired items are
never revived, only recomputed. Fixtures and helpers are the propagation tests'.
"""

from __future__ import annotations

from tests.propagation.test_propagation import (
    accept,
    alerts,
    change_sets,
    column,
    dw,
    edit_column,
    get_kpi,
    labelled,
    map_column,
    mapping_sql,
    only_closed,
    only_propagation,
    problems,
    propagations,
    reject,
)
from tests.roles import RoleClients


def fix_impacts(roles: RoleClients, role="editor"):
    return roles.client(role).post(f"/api/v1/workspaces/{roles.workspace_id}/fix-impacts")


def fixed(roles: RoleClients) -> dict:
    response = fix_impacts(roles)
    assert response.status_code == 200, response.text
    return response.json()


def detail_of(roles: RoleClients, change_set_id: str) -> dict:
    response = roles.client("viewer").get(f"{change_sets(roles)}/{change_set_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_rejected_rename_is_recomputed_and_fixed(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")
    first = only_propagation(warehouse)
    reject(warehouse, first["change_set"]["id"])
    assert len(problems(warehouse, "broken_reference")) == 1

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    assert detail["change_set"]["origin"] == "propagation"
    assert detail["change_set"]["scope"] == {"trigger": "fix_impacts"}
    assert detail["change_set"]["title"] == "Fix impacts: customer_name renamed to full_name"
    assert list(labelled(detail)) == [
        "[Mart] mart.dim_customer_m.customer_name: rename to full_name (optional)",
        "[Mart] mart.dim_customer_m.customer_name mapping: follow the rename",
        "[KPIs] KPI Customers: follow the rename",
    ]
    assert {i["label"] for i in result["impacts"]} >= {"dim_customer_m.customer_name"}
    # The rejected items stay rejected: new items are proposed, never the old ones revived.
    assert {i["id"] for i in detail["items"]}.isdisjoint({i["id"] for i in first["items"]})
    assert {i["status"] for i in only_closed(warehouse, first)["items"]} == {"rejected"}

    accept(warehouse, result["change_set_id"])

    assert mapping_sql(warehouse, model["name_m"]) == ("direct", "dim_customer.full_name")
    assert get_kpi(warehouse, model["kpi"])["formula_sql"] == (
        "SELECT COUNT(DISTINCT m.full_name) FROM dim_customer_m AS m"
    )
    assert problems(warehouse, "broken_reference") == []
    # Nothing is broken any more: nothing to propose.
    assert fixed(warehouse) == {"change_set_id": None, "impacts": []}


def test_renames_in_a_row_are_recomputed_to_the_current_name(model, warehouse):
    edit_column(warehouse, model["name"], name="full_name")
    edit_column(warehouse, model["name"], name="client_name")
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    label = "[Mart] mart.dim_customer_m.customer_name mapping: follow the rename"
    mapping = labelled(detail)[label]
    # The mapping still reads the first name: it follows straight to the current one.
    assert mapping["payload"] == {"sql_expression": "dim_customer.client_name"}
    accept(warehouse, result["change_set_id"], [mapping["id"]])
    assert mapping_sql(warehouse, model["name_m"]) == ("direct", "dim_customer.client_name")
    assert problems(warehouse, "broken_reference") == []


def test_items_a_newer_propagation_expired_are_recomputed_not_revived(layered, warehouse):
    from tests.warehouse.test_staging_sync import run, sync

    customer_id = column(warehouse, layered["name"]["table_id"], "customer_id")
    map_column(warehouse, customer_id, "stg_crm_public_customers.id")
    columns = layered["ids"]["customers"]["columns"]
    run(
        warehouse,
        "update src_columns set status = 'source_removed' where id = :id",
        id=columns["name"],
    )
    accept(warehouse, sync(warehouse, layered["system"])["change_set_id"])
    older = only_propagation(warehouse)
    [unmap] = older["items"]
    # The next sync of the same Source System supersedes the pending propagation.
    run(
        warehouse,
        "update src_columns set current_definition = cast(:d as json) where id = :id",
        d='{"data_type": "varchar(20)", "is_nullable": true}',
        id=columns["id"],
    )
    accept(warehouse, sync(warehouse, layered["system"])["change_set_id"])
    newer = only_propagation(warehouse)
    assert newer["change_set"]["id"] != older["change_set"]["id"]
    assert only_closed(warehouse, older)["change_set"]["status"] == "superseded"
    assert only_closed(warehouse, older)["items"][0]["status"] == "expired"
    assert len(problems(warehouse, "removed_input")) == 1

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    [item] = detail["items"]
    assert item["label"] == unmap["label"]
    assert item["id"] != unmap["id"]
    # The expired item stays expired: it is recomputed, never revived.
    assert only_closed(warehouse, older)["items"][0]["status"] == "expired"
    accept(warehouse, result["change_set_id"])
    assert mapping_sql(warehouse, layered["name"]) == ("unmapped", "")
    assert problems(warehouse, "removed_input") == []


def test_a_mapping_a_later_propagation_left_broken_is_still_fixed(model, warehouse):
    label = column(warehouse, model["mart"]["id"], "label")
    map_column(
        warehouse, label, "CONCAT(dim_customer.customer_name, dim_customer.segment)", "derived"
    )
    edit_column(warehouse, model["name"], name="full_name")
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])
    edit_column(warehouse, model["segment"], name="tier")
    # Accepting re-derives the mapping, which still reads the first name: the mapping now
    # carries a stored broken_reference error.
    accept(warehouse, only_propagation(warehouse)["change_set"]["id"])
    assert mapping_sql(warehouse, label) == (
        "derived",
        "CONCAT(dim_customer.customer_name, dim_customer.tier)",
    )
    assert "label" in [p["column_name"] for p in problems(warehouse, "broken_reference")]

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    item = labelled(detail)["[Mart] mart.dim_customer_m.label mapping: follow the rename"]
    assert item["payload"] == {
        "sql_expression": "CONCAT(dim_customer.full_name, dim_customer.tier)"
    }
    accept(warehouse, result["change_set_id"])
    assert problems(warehouse, "broken_reference") == []


def test_a_partly_accepted_propagation_leaves_nothing_to_fix(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")
    detail = only_propagation(warehouse)
    accept(warehouse, detail["change_set"]["id"], [detail["items"][1]["id"]])

    # The optional rename expired, but nothing is broken: it is not proposed again.
    assert fixed(warehouse) == {"change_set_id": None, "impacts": []}
    assert [c["status"] for c in propagations(warehouse)] == ["partially_applied"]


def test_a_deleted_input_is_recomputed_as_an_unmap(model, warehouse: RoleClients):
    response = warehouse.client("editor").delete(
        f"{dw(warehouse)}/tables/{model['core']['id']}/columns/{model['email']['id']}"
    )
    assert response.status_code == 204, response.text
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    [item] = detail["items"]
    assert item["label"] == (
        "[Mart] mart.dim_customer_m.email_domain mapping: unmap (reads a removed input)"
    )
    accept(warehouse, result["change_set_id"])
    assert mapping_sql(warehouse, model["domain_m"]) == ("unmapped", "")
    assert problems(warehouse, "broken_reference") == []


def test_a_table_rename_is_recomputed_through_branches(model, warehouse: RoleClients):
    branch = warehouse.client("editor").post(
        f"{dw(warehouse)}/tables/{model['mart']['id']}/mapping/branches",
        json={"name": "All", "driving_input": "dim_customer AS c", "filters": "c.segment <> 'x'"},
    )
    assert branch.status_code == 201, branch.text
    current = warehouse.client("viewer").get(f"{dw(warehouse)}/tables/{model['core']['id']}")
    response = warehouse.client("editor").patch(
        f"{dw(warehouse)}/tables/{model['core']['id']}",
        json={"version": current.json()["version"], "name": "dim_client"},
    )
    assert response.status_code == 200, response.text
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    types = [i["object_type"] for i in detail["items"]]
    assert types.count("column_mapping") == 3 and types.count("mapping_branch") == 1
    accept(warehouse, result["change_set_id"])
    assert mapping_sql(warehouse, model["domain_m"]) == (
        "derived",
        "SPLIT_PART(dim_client.email, '@', 2)",
    )
    assert problems(warehouse, "broken_reference") == []


def test_a_source_removed_input_is_recomputed(layered, warehouse: RoleClients):
    from tests.warehouse.test_staging_sync import run, sync

    run(
        warehouse,
        "update src_columns set status = 'source_removed' where id = :id",
        id=layered["ids"]["customers"]["columns"]["name"],
    )
    accept(warehouse, sync(warehouse, layered["system"])["change_set_id"])
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])
    assert len(problems(warehouse, "removed_input")) == 1

    result = fixed(warehouse)

    detail = detail_of(warehouse, result["change_set_id"])
    assert [i["label"] for i in detail["items"]] == [
        "[Core] core.dim_customer.name mapping: unmap (reads a removed input)"
    ]
    accept(warehouse, result["change_set_id"])
    assert problems(warehouse, "removed_input") == []


def test_a_newer_fix_supersedes_a_pending_one_and_raises_an_alert(model, warehouse):
    edit_column(warehouse, model["name"], name="full_name")
    reject(warehouse, only_propagation(warehouse)["change_set"]["id"])
    before = len(alerts(warehouse))

    first = fixed(warehouse)
    second = fixed(warehouse)

    assert first["change_set_id"] != second["change_set_id"]
    statuses = {c["id"]: c["status"] for c in propagations(warehouse)}
    assert statuses[first["change_set_id"]] == "superseded"
    assert statuses[second["change_set_id"]] == "pending"
    assert {i["status"] for i in detail_of(warehouse, first["change_set_id"])["items"]} == {
        "expired"
    }
    assert len(alerts(warehouse)) == before + 2
    assert second["change_set_id"] in {a["ref_id"] for a in alerts(warehouse)}


def test_fix_impacts_needs_an_editor_and_an_active_workspace(model, warehouse: RoleClients):
    edit_column(warehouse, model["name"], name="full_name")
    assert fix_impacts(warehouse, "viewer").status_code == 403

    workspace = f"/api/v1/workspaces/{warehouse.workspace_id}"
    archived = warehouse.client("owner").post(f"{workspace}/archive")
    assert archived.status_code == 204, archived.text
    # Archiving rejected the pending propagation; nothing can be fixed while archived.
    assert [c["status"] for c in propagations(warehouse)] == ["rejected"]
    refused = fix_impacts(warehouse)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "workspace_archived"

    unarchived = warehouse.client("owner").post(
        f"/api/v1/workspaces/{warehouse.workspace_id}/unarchive"
    )
    assert unarchived.status_code == 204, unarchived.text
    # After unarchiving, "Fix impacts" recomputes what archiving left broken.
    result = fixed(warehouse)
    statuses = {c["id"]: c["status"] for c in propagations(warehouse)}
    assert statuses[result["change_set_id"]] == "pending"
