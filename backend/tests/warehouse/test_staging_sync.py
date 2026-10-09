"""Staging sync and "drop removed" (spec §6.7, stories 86, 86a), through the HTTP API.

The Source Schema is seeded and changed straight in the sources module's tables (extraction
itself is covered elsewhere); one test follows a real extraction to show the sync runs by
itself after a new Snapshot.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from tests.roles import RoleClients
from tests.sources.test_extraction import add_system as add_extraction_system
from tests.sources.test_extraction import connect, extract
from tests.warehouse.test_staging_generation import (
    add_system,
    base,
    columns_of,
    generate,
    query,
    seed,
    staging_tables,
)


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


CUSTOMER = [("id", "integer"), ("name", "varchar(40)")]


def change_sets(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/change-sets"


def sync(roles: RoleClients, system_id: uuid.UUID, role: str = "editor") -> dict:
    response = roles.client(role).post(
        f"{base(roles)}/staging/sync", json={"source_system_id": str(system_id)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def detail(roles: RoleClients, change_set_id: str) -> dict:
    response = roles.client("viewer").get(f"{change_sets(roles)}/{change_set_id}")
    assert response.status_code == 200, response.text
    return response.json()


def items_of(roles: RoleClients, proposed: dict) -> list[dict]:
    return detail(roles, proposed["change_set_id"])["items"]


def accept(roles: RoleClients, change_set_id: str, role: str = "editor") -> dict:
    response = roles.client(role).post(f"{change_sets(roles)}/{change_set_id}/accept", json={})
    assert response.status_code == 200, response.text
    return response.json()


def run(roles: RoleClients, sql: str, **params) -> None:
    with roles.app.state.engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def staged(roles: RoleClients, system_id: uuid.UUID, tables: dict | None = None) -> dict:
    """Seed ``tables`` (default: customers) and generate staging, returning the seed ids."""
    ids = seed(roles, system_id, "public", tables or {"customers": CUSTOMER})
    assert generate(roles).status_code == 200
    return ids


def test_a_first_sync_proposes_new_tables_and_changes_nothing_until_confirmed(
    warehouse: RoleClients,
):
    system = add_system(warehouse)
    seed(warehouse, system, "public", {"customers": CUSTOMER, "orders": [("id", "integer")]})

    proposed = sync(warehouse, system)

    assert (proposed["items"], proposed["conflicts"]) == (2, 0)
    [change_set] = warehouse.client("viewer").get(change_sets(warehouse)).json()["items"]
    assert (change_set["origin"], change_set["status"]) == ("sync", "pending")
    assert staging_tables(warehouse) == {}
    items = items_of(warehouse, proposed)
    assert {i["label"] for i in items} == {"stg_crm_public_customers", "stg_crm_public_orders"}
    assert all(i["object_type"] == "staging_table" and i["operation"] == "create" for i in items)

    body = accept(warehouse, proposed["change_set_id"])

    assert len(body["accepted"]) == 2
    tables = staging_tables(warehouse)
    assert set(tables) == {"stg_crm_public_customers", "stg_crm_public_orders"}
    columns = columns_of(warehouse, tables["stg_crm_public_customers"])
    assert list(columns) == ["id", "name", "load_ts", "source_system"]
    assert columns["name"]["data_type"]["length"] == 40
    [count] = query(
        warehouse,
        "select count(*) from lineage_edges where to_type = 'dw_column' and kind = 'value'",
    )[0]
    assert count == 3


def test_syncing_what_is_already_staged_proposes_nothing(warehouse: RoleClients):
    system = add_system(warehouse)
    staged(warehouse, system)

    proposed = sync(warehouse, system)

    assert proposed == {"change_set_id": None, "items": 0, "conflicts": 0}


def test_a_newer_sync_supersedes_a_pending_one(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "public", {"customers": CUSTOMER})
    first = sync(warehouse, system)

    seed(warehouse, system, "public", {"orders": [("id", "integer")]})
    second = sync(warehouse, system)

    assert first["change_set_id"] != second["change_set_id"]
    assert detail(warehouse, first["change_set_id"])["change_set"]["status"] == "superseded"
    assert detail(warehouse, second["change_set_id"])["change_set"]["status"] == "pending"
    assert second["items"] == 2


def test_a_sync_raises_a_sync_alert_for_owners_and_editors(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "public", {"customers": CUSTOMER})
    warehouse.client("viewer")

    proposed = sync(warehouse, system)

    for role, expected in (("editor", 1), ("owner", 1), ("viewer", 0)):
        unread = warehouse.client(role).get("/api/v1/notifications").json()["items"]
        alerts = [n for n in unread if n["kind"] == "sync_alert"]
        assert len(alerts) == expected, role
        if expected:
            assert alerts[0]["ref_id"] == proposed["change_set_id"]


def test_new_and_changed_source_columns_are_synced_into_existing_staging(
    warehouse: RoleClients,
):
    system = add_system(warehouse)
    ids = staged(warehouse, system)
    table = ids["customers"]
    run(
        warehouse,
        "update src_columns set current_definition = cast(:d as json) where id = :id",
        d='{"data_type": "bigint", "is_nullable": false}',
        id=table["columns"]["id"],
    )
    run(
        warehouse,
        "insert into src_columns (id, table_id, name, current_definition, status, version)"
        " values (:id, :t, 'email', cast(:d as json), 'present', 1)",
        id=uuid.uuid4(),
        t=table["id"],
        d='{"data_type": "varchar(80)", "is_nullable": true}',
    )

    proposed = sync(warehouse, system)

    items = items_of(warehouse, proposed)
    assert sorted((i["object_type"], i["operation"]) for i in items) == [
        ("staging_column", "create"),
        ("staging_column", "update"),
    ]
    accept(warehouse, proposed["change_set_id"])
    columns = columns_of(warehouse, staging_tables(warehouse)["stg_crm_public_customers"])
    assert list(columns) == ["id", "name", "email", "load_ts", "source_system"]
    assert columns["id"]["data_type"]["type"] == "bigint" and not columns["id"]["is_nullable"]
    assert columns["email"]["data_type"]["length"] == 80
    [mapping] = query(
        warehouse,
        "select m.mapping_type, m.sql_expression from column_mappings m"
        " join dw_columns c on c.id = m.dw_column_id where c.name = 'email'",
    )
    assert tuple(mapping) == ("direct", "public.customers.email")


def test_a_user_override_is_a_conflict_not_overwritten(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = staged(warehouse, system)
    table = staging_tables(warehouse)["stg_crm_public_customers"]
    column = columns_of(warehouse, table)["name"]
    edited = warehouse.client("editor").patch(
        f"{base(warehouse)}/tables/{table['id']}/columns/{column['id']}",
        json={"version": column["version"], "data_type": {"type": "text"}},
    )
    assert edited.status_code == 200, edited.text
    run(
        warehouse,
        "update src_columns set current_definition = cast(:d as json) where id = :id",
        d='{"data_type": "varchar(120)", "is_nullable": true}',
        id=ids["customers"]["columns"]["name"],
    )

    proposed = sync(warehouse, system)

    assert (proposed["items"], proposed["conflicts"]) == (1, 1)
    [item] = items_of(warehouse, proposed)
    assert item["is_conflict"] and item["payload"]["data_type"]["length"] == 120
    # Nothing was overwritten while the item is pending.
    assert columns_of(warehouse, table)["name"]["data_type"]["type"] == "text"

    accept(warehouse, proposed["change_set_id"])

    assert columns_of(warehouse, table)["name"]["data_type"]["length"] == 120
    assert sync(warehouse, system)["items"] == 0


def test_a_removed_source_table_is_kept_and_flagged(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = staged(warehouse, system, {"customers": CUSTOMER, "orders": [("id", "integer")]})
    run(
        warehouse,
        "update src_tables set status = 'source_removed' where id = :id",
        id=ids["orders"]["id"],
    )
    run(
        warehouse,
        "update src_columns set status = 'source_removed' where id = :id",
        id=ids["customers"]["columns"]["name"],
    )

    proposed = sync(warehouse, system)

    items = items_of(warehouse, proposed)
    assert sorted((i["object_type"], i["payload"]["status"]) for i in items) == [
        ("staging_column", "source_removed"),
        ("staging_table", "source_removed"),
    ]
    accept(warehouse, proposed["change_set_id"])
    tables = staging_tables(warehouse)
    assert set(tables) == {"stg_crm_public_customers", "stg_crm_public_orders"}
    assert tables["stg_crm_public_orders"]["status"] == "source_removed"
    assert tables["stg_crm_public_customers"]["status"] == "present"
    columns = columns_of(warehouse, tables["stg_crm_public_customers"])
    assert columns["name"]["status"] == "source_removed" and "name" in columns
    assert sync(warehouse, system)["items"] == 0


def test_a_deleted_staging_table_is_never_proposed_again(warehouse: RoleClients):
    system = add_system(warehouse)
    staged(warehouse, system, {"customers": CUSTOMER, "orders": [("id", "integer")]})
    orders = staging_tables(warehouse)["stg_crm_public_orders"]
    deleted = warehouse.client("editor").delete(f"{base(warehouse)}/tables/{orders['id']}")
    assert deleted.status_code == 204, deleted.text

    proposed = sync(warehouse, system)

    assert proposed["change_set_id"] is None
    assert generate(warehouse).json()["tables_created"] == 0
    assert set(staging_tables(warehouse)) == {"stg_crm_public_customers"}
    [tombstone] = query(warehouse, "select object_type from tombstones")
    assert tombstone[0] == "staging_table"


def test_a_renamed_source_table_keeps_its_staging_identity(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = staged(warehouse, system)
    before = staging_tables(warehouse)["stg_crm_public_customers"]["id"]
    run(
        warehouse,
        "update src_tables set name = 'clients' where id = :id",
        id=ids["customers"]["id"],
    )
    run(
        warehouse,
        "update src_columns set name = 'full_name' where id = :id",
        id=ids["customers"]["columns"]["name"],
    )

    assert sync(warehouse, system)["change_set_id"] is None
    tables = staging_tables(warehouse)
    assert tables["stg_crm_public_customers"]["id"] == before and len(tables) == 1


def test_drop_removed_proposes_owner_only_deletes_of_unused_removed_objects(
    warehouse: RoleClients,
):
    system = add_system(warehouse)
    ids = staged(
        warehouse,
        system,
        {"customers": CUSTOMER, "orders": [("id", "integer")], "legacy": [("id", "integer")]},
    )
    for name in ("orders", "legacy"):
        run(
            warehouse,
            "update src_tables set status = 'source_removed' where id = :id",
            id=ids[name]["id"],
        )
    run(
        warehouse,
        "update src_columns set status = 'source_removed' where id = :id",
        id=ids["customers"]["columns"]["name"],
    )
    accept(warehouse, sync(warehouse, system)["change_set_id"])
    tables = staging_tables(warehouse)
    # "orders" is read downstream by a lineage edge, so it must stay.
    orders_id = columns_of(warehouse, tables["stg_crm_public_orders"])["id"]["id"]
    run(
        warehouse,
        "insert into lineage_edges (id, kind, from_type, from_id, to_type, to_id)"
        " values (:id, 'value', 'dw_column', :f, 'dw_column', :t)",
        id=uuid.uuid4(),
        f=orders_id,
        t=uuid.uuid4(),
    )

    response = warehouse.client("editor").post(f"{base(warehouse)}/staging/drop-removed")
    assert response.status_code == 200, response.text
    proposed = response.json()

    items = items_of(warehouse, proposed)
    assert sorted(i["label"] for i in items) == [
        "stg_crm_public_customers.name",
        "stg_crm_public_legacy",
    ]
    assert all(i["operation"] == "delete" and i["required_role"] == "owner" for i in items)

    by_editor = accept(warehouse, proposed["change_set_id"])
    assert by_editor["accepted"] == [] and len(by_editor["needs_owner"]) == 2
    assert set(staging_tables(warehouse)) == set(tables)

    accept(warehouse, proposed["change_set_id"], role="owner")

    remaining = staging_tables(warehouse)
    assert set(remaining) == {"stg_crm_public_customers", "stg_crm_public_orders"}
    assert "name" not in columns_of(warehouse, remaining["stg_crm_public_customers"])
    [tombstone] = query(warehouse, "select src_object_id from tombstones")
    assert tombstone[0] == ids["legacy"]["id"]
    assert sync(warehouse, system)["change_set_id"] is None


def test_drop_removed_with_nothing_removed_proposes_nothing(warehouse: RoleClients):
    system = add_system(warehouse)
    staged(warehouse, system)

    response = warehouse.client("owner").post(f"{base(warehouse)}/staging/drop-removed")

    assert response.json() == {"change_set_id": None, "items": 0, "conflicts": 0}


def test_syncing_an_unknown_source_system_is_not_found(warehouse: RoleClients):
    response = warehouse.client("editor").post(
        f"{base(warehouse)}/staging/sync", json={"source_system_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404


def test_a_new_snapshot_raises_a_sync_alert_and_a_sync_change_set(
    warehouse: RoleClients, sample_source
):
    system = add_extraction_system(warehouse)
    connect(warehouse, system, sample_source.connection_body())

    job = extract(warehouse, system)

    assert job["status"] == "succeeded"
    [change_set] = warehouse.client("viewer").get(change_sets(warehouse)).json()["items"]
    assert (change_set["origin"], change_set["status"]) == ("sync", "pending")
    assert staging_tables(warehouse) == {}
    unread = warehouse.client("editor").get("/api/v1/notifications").json()["items"]
    assert [n["kind"] for n in unread if n["kind"] == "sync_alert"] == ["sync_alert"]


@pytest.mark.parametrize("role", ["viewer"])
def test_a_viewer_cannot_propose_a_sync(warehouse: RoleClients, role: str):
    system = add_system(warehouse)

    response = warehouse.client(role).post(
        f"{base(warehouse)}/staging/sync", json={"source_system_id": str(system)}
    )

    assert response.status_code == 403
