"""Soft deletes of Source Systems and DW tables, and confirming the permanent delete (spec §7
"Soft deletes", §4.3 "Delete Source Systems or DW tables (soft, then confirm)"), through the
HTTP API. The Source Schema is seeded straight in the sources module's tables, as the staging
tests do.
"""

from __future__ import annotations

import uuid

import pytest

from tests.files.test_files import upload
from tests.kpis.test_kpis import created, kpis_path
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import (
    add_system,
    base,
    generate,
    query,
    seed,
    staging_tables,
)

CUSTOMER = [("id", "integer"), ("name", "varchar(40)")]


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def systems(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/systems"


def deleted(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/deleted"


def table(roles: RoleClients, name: str, layer: str = "core", kind: str = "dimension") -> dict:
    body = {"layer": layer, "name": name, "kind": kind}
    if kind == "fact":
        body |= {"grain": "One row per line", "fact_type": "transactional"}
    response = roles.client("editor").post(f"{base(roles)}/tables", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def measure(roles: RoleClients, table_id: str, name: str) -> dict:
    response = roles.client("editor").post(
        f"{base(roles)}/tables/{table_id}/columns",
        json={
            "name": name,
            "data_type": {"type": "decimal", "precision": 18, "scale": 2},
            "role": "measure",
            "additivity": "additive",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_branch(roles: RoleClients, table_id: str, name: str, driving_input: str) -> dict:
    response = roles.client("editor").post(
        f"{base(roles)}/tables/{table_id}/mapping/branches",
        json={"name": name, "driving_input": driving_input},
    )
    assert response.status_code == 201, response.text
    return next(b for b in response.json()["branches"] if b["name"] == name)


def link(roles: RoleClients, kpi: dict, column_ids: list[str]) -> None:
    response = roles.client("editor").put(
        f"{kpis_path(roles)}/{kpi['id']}/links",
        json={"version": kpi["version"], "dw_column_ids": column_ids},
    )
    assert response.status_code == 200, response.text


def problems(roles: RoleClients) -> list[dict]:
    response = roles.client("viewer").get(f"{base(roles)}/validation")
    assert response.status_code == 200, response.text
    return response.json()["problems"]


def test_only_an_owner_soft_deletes_a_source_system(roles: RoleClients):
    system = add_system(roles, "crm")

    assert roles.client("editor").delete(f"{systems(roles)}/{system}").status_code == 403
    assert roles.client("owner").delete(f"{systems(roles)}/{system}").status_code == 204

    assert roles.client("viewer").get(f"{systems(roles)}/{system}").status_code == 404
    assert roles.client("viewer").get(systems(roles)).json()["items"] == []
    listed = roles.client("viewer").get(deleted(roles))
    assert listed.status_code == 200, listed.text
    assert [(s["id"], s["code"]) for s in listed.json()["systems"]] == [(str(system), "crm")]
    assert roles.client("owner").delete(f"{systems(roles)}/{system}").status_code == 404


def test_a_deleted_system_code_is_reusable_only_after_the_permanent_delete(roles: RoleClients):
    system = add_system(roles, "crm")
    roles.client("owner").delete(f"{systems(roles)}/{system}")

    taken = roles.client("owner").post(systems(roles), json={"name": "CRM 2", "code": "crm"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "system_code_taken"

    assert roles.client("editor").delete(f"{deleted(roles)}/systems/{system}").status_code == 403
    assert roles.client("owner").delete(f"{deleted(roles)}/systems/{system}").status_code == 204

    reused = roles.client("owner").post(systems(roles), json={"name": "CRM 2", "code": "crm"})
    assert reused.status_code == 201, reused.text
    assert roles.client("viewer").get(deleted(roles)).json()["systems"] == []


def test_purging_a_system_takes_what_exists_only_because_of_it(warehouse: RoleClients):
    crm = add_system(warehouse, "crm")
    cbs = add_system(warehouse, "cbs")
    seed(warehouse, crm, "public", {"customers": CUSTOMER})
    seed(warehouse, cbs, "public", {"customers": CUSTOMER})
    assert generate(warehouse).status_code == 200
    customer = table(warehouse, "core_customer")
    crm_branch = add_branch(warehouse, customer["id"], "CRM", "stg_crm_public_customers")
    cbs_branch = add_branch(warehouse, customer["id"], "Core Banking", "stg_cbs_public_customers")
    sales = table(warehouse, "fact_sales", kind="fact")
    amount = measure(warehouse, sales["id"], "amount")
    linked = created(warehouse, name="Revenue", source_system_id=str(crm))
    link(warehouse, linked, [amount["id"]])
    unlinked = created(warehouse, name="Churn", source_system_id=str(crm))
    other = created(warehouse, name="Deposits", source_system_id=str(cbs))
    assert upload(warehouse, str(crm), "notes.txt", b"Customer notes").status_code == 201
    owner = warehouse.client("owner")
    assert owner.delete(f"{systems(warehouse)}/{crm}").status_code == 204

    preview = owner.get(f"{deleted(warehouse)}/systems/{crm}")

    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["system"]["code"] == "crm"
    assert (shown["table_count"], shown["column_count"]) == (1, 2)
    assert [t["name"] for t in shown["staging_tables"]] == ["stg_crm_public_customers"]
    assert [(b["table_name"], b["branch_id"]) for b in shown["broken_branches"]] == [
        ("core_customer", crm_branch["id"])
    ]
    assert [f["path"] for f in shown["files"]] == ["notes.txt"]
    assert [k["name"] for k in shown["rehomed_kpis"]] == ["Revenue"]
    assert [k["name"] for k in shown["deleted_kpis"]] == ["Churn"]
    assert warehouse.client("editor").get(f"{deleted(warehouse)}/systems/{crm}").status_code == 403

    assert owner.delete(f"{deleted(warehouse)}/systems/{crm}").status_code == 204

    assert set(staging_tables(warehouse)) == {"stg_cbs_public_customers"}
    rehomed = owner.get(f"{kpis_path(warehouse)}/{linked['id']}").json()
    assert rehomed["source_system_id"] is None
    assert owner.get(f"{kpis_path(warehouse)}/{unlinked['id']}").status_code == 404
    assert owner.get(f"{kpis_path(warehouse)}/{other['id']}").json()["source_system_id"] == str(cbs)
    mapping = owner.get(f"{base(warehouse)}/tables/{customer['id']}/mapping").json()
    assert [b["id"] for b in mapping["branches"]] == [crm_branch["id"], cbs_branch["id"]]
    broken = [p for p in problems(warehouse) if p["code"] == "broken_reference"]
    assert {p["branch_id"] for p in broken} == {crm_branch["id"]}
    for leftover in ("src_db_schemas", "snapshots", "workspace_files"):
        [(count,)] = query(
            warehouse,
            f"select count(*) from {leftover} where "
            + ("source_system_id" if leftover != "workspace_files" else "owner_id")
            + " = :s",
            s=crm,
        )
        assert count == 0, leftover
    feed = owner.get(f"/api/v1/workspaces/{warehouse.workspace_id}/activity").json()["items"]
    verbs = [item["verb"] for item in feed]
    assert "source_system.deleted" in verbs and "source_system.purged" in verbs


def test_only_an_owner_soft_deletes_a_dw_table_and_it_leaves_the_model(warehouse: RoleClients):
    customer = table(warehouse, "dim_customer")
    path = f"{base(warehouse)}/tables/{customer['id']}"

    assert warehouse.client("editor").delete(path).status_code == 403
    assert warehouse.client("owner").delete(path).status_code == 204

    viewer = warehouse.client("viewer")
    assert viewer.get(path).status_code == 404
    assert viewer.get(f"{base(warehouse)}/tables").json()["items"] == []
    [shown] = viewer.get(deleted(warehouse)).json()["tables"]
    assert (shown["id"], shown["layer"], shown["name"]) == (customer["id"], "core", "dim_customer")


def test_a_branch_reading_a_deleted_table_shows_an_error_and_the_others_stay(
    warehouse: RoleClients,
):
    crm = table(warehouse, "crm_customer")
    cb = table(warehouse, "cb_customer")
    mart = table(warehouse, "dim_customer", "mart")
    crm_branch = add_branch(warehouse, mart["id"], "CRM", "crm_customer")
    cb_branch = add_branch(warehouse, mart["id"], "Core Banking", "cb_customer")
    owner = warehouse.client("owner")
    assert owner.delete(f"{base(warehouse)}/tables/{crm['id']}").status_code == 204

    broken = [p for p in problems(warehouse) if p["code"] == "broken_reference"]
    assert {p["branch_id"] for p in broken} == {crm_branch["id"]}

    preview = owner.get(f"{deleted(warehouse)}/tables/{crm['id']}")
    assert preview.status_code == 200, preview.text
    assert [b["branch_id"] for b in preview.json()["broken_branches"]] == [crm_branch["id"]]

    assert owner.delete(f"{deleted(warehouse)}/tables/{crm['id']}").status_code == 204

    assert owner.get(deleted(warehouse)).json()["tables"] == []
    mapping = owner.get(f"{base(warehouse)}/tables/{mart['id']}/mapping").json()
    assert [b["id"] for b in mapping["branches"]] == [crm_branch["id"], cb_branch["id"]]
    assert owner.get(f"{base(warehouse)}/tables/{cb['id']}").status_code == 200


def test_purging_a_table_names_the_kpis_that_lose_their_links(warehouse: RoleClients):
    sales = table(warehouse, "fact_sales", kind="fact")
    amount = measure(warehouse, sales["id"], "amount")
    kpi = created(warehouse, name="Revenue")
    link(warehouse, kpi, [amount["id"]])
    owner = warehouse.client("owner")
    owner.delete(f"{base(warehouse)}/tables/{sales['id']}")

    preview = owner.get(f"{deleted(warehouse)}/tables/{sales['id']}").json()

    assert preview["column_names"] == ["amount"]
    assert [k["name"] for k in preview["unlinked_kpis"]] == ["Revenue"]
    assert owner.delete(f"{deleted(warehouse)}/tables/{sales['id']}").status_code == 204
    links = owner.get(f"{kpis_path(warehouse)}/{kpi['id']}/links").json()
    assert links["items"] == []


def test_a_deleted_generated_or_staging_table_keeps_a_tombstone(warehouse: RoleClients):
    system = add_system(warehouse, "crm")
    seed(warehouse, system, "public", {"customers": CUSTOMER})
    assert generate(warehouse).status_code == 200
    staging = staging_tables(warehouse)["stg_crm_public_customers"]
    owner = warehouse.client("owner")

    assert owner.delete(f"{base(warehouse)}/tables/{staging['id']}").status_code == 204
    assert owner.delete(f"{deleted(warehouse)}/tables/{staging['id']}").status_code == 204

    assert [r[0] for r in query(warehouse, "select object_type from tombstones")] == [
        "staging_table"
    ]
    assert generate(warehouse).json()["tables_created"] == 0


def test_an_unknown_or_present_object_cannot_be_purged(warehouse: RoleClients):
    present = table(warehouse, "dim_customer")
    owner = warehouse.client("owner")

    assert owner.get(f"{deleted(warehouse)}/tables/{present['id']}").status_code == 404
    assert owner.delete(f"{deleted(warehouse)}/tables/{present['id']}").status_code == 404
    assert owner.delete(f"{deleted(warehouse)}/systems/{uuid.uuid4()}").status_code == 404
