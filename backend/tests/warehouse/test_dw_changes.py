"""System Code and target platform changes as Change Sets (spec §6.7, §6.9, stories 39, 87),
through the HTTP API."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import (
    add_system,
    base,
    columns_of,
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


def change_sets(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/change-sets"


def code_change(roles: RoleClients, system_id: uuid.UUID, code: str, role: str = "owner"):
    return roles.client(role).post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id}/code-change",
        json={"code": code},
    )


def platform_change(roles: RoleClients, platform: str, role: str = "owner"):
    return roles.client(role).post(
        f"{base(roles)}/platform-change", json={"target_platform": platform}
    )


def detail(roles: RoleClients, change_set_id: str) -> dict:
    response = roles.client("viewer").get(f"{change_sets(roles)}/{change_set_id}")
    assert response.status_code == 200, response.text
    return response.json()


def accept(roles: RoleClients, change_set_id: str, role: str = "owner") -> dict:
    response = roles.client(role).post(f"{change_sets(roles)}/{change_set_id}/accept", json={})
    assert response.status_code == 200, response.text
    return response.json()


def staged_system(roles: RoleClients) -> uuid.UUID:
    system = add_system(roles)
    seed(roles, system, "public", {"customers": CUSTOMER})
    assert generate(roles).status_code == 200
    return system


def test_only_an_owner_proposes_a_system_code_change(warehouse: RoleClients):
    system = staged_system(warehouse)

    assert code_change(warehouse, system, "crm2", role="editor").status_code == 403
    assert code_change(warehouse, system, "crm2", role="viewer").status_code == 403
    assert code_change(warehouse, system, "crm2").status_code == 200


def test_a_system_code_change_is_one_rename_change_set(warehouse: RoleClients):
    system = staged_system(warehouse)

    proposed = code_change(warehouse, system, "crm2").json()

    body = detail(warehouse, proposed["change_set_id"])
    assert body["change_set"]["origin"] == "system_code_change"
    assert body["change_set"]["status"] == "pending"
    kinds = sorted((i["object_type"], i["required_role"]) for i in body["items"])
    assert kinds == [
        ("source_system", "owner"),
        ("staging_column", "editor"),
        ("staging_table", "editor"),
    ]
    assert "stg_crm_public_customers" in staging_tables(warehouse)  # nothing changed yet

    accepted = accept(warehouse, proposed["change_set_id"])

    assert len(accepted["accepted"]) == 3
    tables = staging_tables(warehouse)
    assert set(tables) == {"stg_crm2_public_customers"}
    audit = columns_of(warehouse, tables["stg_crm2_public_customers"])["source_system"]
    [expression] = query(
        warehouse,
        "select sql_expression from column_mappings where dw_column_id = :c",
        c=audit["id"],
    )[0]
    assert expression == "'crm2'"
    [code] = query(warehouse, "select code from source_systems")[0]
    assert code == "crm2"


def test_an_editor_cannot_accept_the_code_but_the_set_waits_for_an_owner(warehouse: RoleClients):
    system = staged_system(warehouse)
    proposed = code_change(warehouse, system, "crm2").json()

    result = accept(warehouse, proposed["change_set_id"], role="editor")

    assert result["accepted"] == []
    assert len(result["needs_owner"]) == 3  # the renames depend on the code


def test_a_staged_system_cannot_change_its_code_directly(warehouse: RoleClients):
    system = staged_system(warehouse)
    systems = f"/api/v1/workspaces/{warehouse.workspace_id}/systems/{system}"
    version = warehouse.client("owner").get(systems).json()["version"]

    response = warehouse.client("owner").patch(systems, json={"version": version, "code": "crm2"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "use_change_set"


def test_a_system_without_staging_changes_its_code_directly(warehouse: RoleClients):
    system = add_system(warehouse)
    systems = f"/api/v1/workspaces/{warehouse.workspace_id}/systems/{system}"
    version = warehouse.client("owner").get(systems).json()["version"]

    response = warehouse.client("owner").patch(systems, json={"version": version, "code": "crm2"})

    assert response.status_code == 200, response.text


def test_a_platform_change_is_owner_only_and_one_change_set(warehouse: RoleClients):
    staged_system(warehouse)

    assert platform_change(warehouse, "sqlserver", role="editor").status_code == 403
    proposed = platform_change(warehouse, "sqlserver").json()

    body = detail(warehouse, proposed["change_set_id"])
    assert body["change_set"]["origin"] == "platform_change"
    [platform] = [i for i in body["items"] if i["object_type"] == "data_warehouse"]
    assert platform["required_role"] == "owner"
    assert platform["payload"] == {"target_platform": "sqlserver"}

    accept(warehouse, proposed["change_set_id"])

    [current] = query(warehouse, "select target_platform from data_warehouses")[0]
    assert current == "sqlserver"


def test_a_platform_change_translates_the_staging_types_again(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "public", {"docs": [("body", "varchar(5000)"), ("n", "integer")]})
    assert generate(warehouse).status_code == 200

    proposed = platform_change(warehouse, "oracle").json()

    items = detail(warehouse, proposed["change_set_id"])["items"]
    retyped = {i["label"] for i in items if i["object_type"] == "staging_column"}
    assert retyped == {"stg_crm_public_docs.body"}  # longer than an Oracle varchar
    accept(warehouse, proposed["change_set_id"])
    table = staging_tables(warehouse)["stg_crm_public_docs"]
    assert columns_of(warehouse, table)["body"]["data_type"]["type"] == "text"


def test_untranslatable_sql_is_flagged_manual_and_left_alone(warehouse: RoleClients):
    staged_system(warehouse)
    table = staging_tables(warehouse)["stg_crm_public_customers"]
    column = columns_of(warehouse, table)["name"]
    text = "STRING_AGG(x, ',' ORDER BY y)"
    with warehouse.app.state.engine.begin() as connection:
        # A Core mapping carries the text (staging mappings hold source-dialect SQL).
        connection.execute(
            sa.text("update dw_tables set layer = 'core' where id = :t"), {"t": table["id"]}
        )
        connection.execute(
            sa.text("update column_mappings set sql_expression = :sql where dw_column_id = :c"),
            {"sql": text, "c": column["id"]},
        )

    assert platform_change(warehouse, "postgresql").status_code == 409  # on it already
    proposed = platform_change(warehouse, "bigquery")

    assert proposed.status_code == 200, proposed.text
    [note] = proposed.json()["manual"]
    assert "postgresql to bigquery" in note
    items = detail(warehouse, proposed.json()["change_set_id"])["items"]
    [manual] = [i for i in items if i["object_type"] == "manual_review"]
    assert manual["payload"]["sql"] == text
    accept(warehouse, proposed.json()["change_set_id"])
    [stored] = query(
        warehouse,
        "select sql_expression from column_mappings where dw_column_id = :c",
        c=column["id"],
    )[0]
    assert stored == text


def test_a_warehouse_with_tables_cannot_change_platform_directly(warehouse: RoleClients):
    staged_system(warehouse)
    version = warehouse.client("owner").get(base(warehouse)).json()["version"]

    response = warehouse.client("owner").patch(
        base(warehouse), json={"version": version, "target_platform": "oracle"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "use_change_set"


def test_a_platform_change_before_setup_is_refused(roles: RoleClients):
    response = platform_change(roles, "oracle")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_set_up"
