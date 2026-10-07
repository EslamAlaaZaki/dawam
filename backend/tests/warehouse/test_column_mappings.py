"""Column mappings and lineage edges (spec §6.14, stories 99, 100, 104)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from dawam.modules.audit import AuditService
from tests.roles import RoleClients


def base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def table(roles: RoleClients, name: str, layer: str, kind="dimension") -> dict:
    body = {"layer": layer, "name": name, "kind": kind}
    if kind == "fact":
        body |= {"grain": "One row per line", "fact_type": "transactional"}
    response = roles.client("editor").post(f"{base(roles)}/tables", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def column(roles: RoleClients, table_id: str, name: str, type_="string", role="attribute") -> dict:
    response = roles.client("editor").post(
        f"{base(roles)}/tables/{table_id}/columns",
        json={"name": name, "data_type": {"type": type_}, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def model(warehouse: RoleClients) -> dict:
    """Core ``dim_customer`` (first_name, last_name) and Mart ``dim_customer_m``."""
    core = table(warehouse, "dim_customer", "core")
    mart = table(warehouse, "dim_customer_m", "mart")
    return {
        "core": core,
        "mart": mart,
        "first": column(warehouse, core["id"], "first_name"),
        "last": column(warehouse, core["id"], "last_name"),
        "status": column(warehouse, core["id"], "status"),
        "full": column(warehouse, mart["id"], "full_name"),
        "name": column(warehouse, mart["id"], "name"),
    }


def mapping_path(roles: RoleClients, table_id: str) -> str:
    return f"{base(roles)}/tables/{table_id}/mapping"


def put(roles: RoleClients, model: dict, target: str, role="editor", **body):
    path = f"{mapping_path(roles, model['mart']['id'])}/columns/{model[target]['id']}"
    return roles.client(role).put(path, json=body)


def get_mapping(roles: RoleClients, table_id: str) -> dict:
    response = roles.client("viewer").get(mapping_path(roles, table_id))
    assert response.status_code == 200, response.text
    return response.json()


def edges(roles: RoleClients, column_id: str, direction="upstream") -> list[dict]:
    response = roles.client("viewer").get(
        f"{base(roles)}/lineage/columns/{column_id}", params={"direction": direction}
    )
    assert response.status_code == 200, response.text
    return response.json()["edges"]


def test_an_unmapped_table_lists_every_column_as_unmapped(warehouse, model):
    mapping = get_mapping(warehouse, model["mart"]["id"])

    assert mapping["source_layer"] == "core"
    assert mapping["version"] == 0
    assert {c["column_name"]: c["mapping_type"] for c in mapping["columns"]} == {
        "dim_customer_m_key": "system",
        "full_name": "unmapped",
        "name": "unmapped",
    }


def test_a_derived_mapping_stores_its_sql_and_derives_inputs_and_edges(warehouse, model):
    response = put(
        warehouse,
        model,
        "full",
        mapping_type="derived",
        rule_text="First and last name together",
        sql_expression="dim_customer.first_name || ' ' || dim_customer.last_name",
    )

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 1
    assert saved["validation"] == {"unparsed": False, "errors": []}
    assert [i["column_name"] for i in saved["inputs"]] == ["first_name", "last_name"]
    assert saved["inputs"][0]["table_name"] == "dim_customer"
    upstream = edges(warehouse, model["full"]["id"])
    assert {(e["kind"], e["from_label"]) for e in upstream} == {
        ("value", "dim_customer.first_name"),
        ("value", "dim_customer.last_name"),
    }
    assert {e["to_id"] for e in upstream} == {model["full"]["id"]}


def test_saving_again_replaces_the_previous_edges(warehouse, model):
    put(warehouse, model, "full", mapping_type="direct", sql_expression="dim_customer.first_name")

    again = put(
        warehouse,
        model,
        "full",
        version=1,
        mapping_type="direct",
        sql_expression="dim_customer.last_name",
    )

    assert again.status_code == 200, again.text
    assert again.json()["version"] == 2
    assert [e["from_label"] for e in edges(warehouse, model["full"]["id"])] == [
        "dim_customer.last_name"
    ]


def test_columns_that_only_steer_the_result_are_uses_edges_to_the_table(warehouse, model):
    put(
        warehouse,
        model,
        "name",
        mapping_type="derived",
        sql_expression="CASE WHEN dim_customer.status = 'A' THEN dim_customer.first_name END",
    )

    upstream = edges(warehouse, model["name"]["id"])

    assert {(e["kind"], e["from_label"], e["to_type"]) for e in upstream} == {
        ("value", "dim_customer.first_name", "dw_column"),
        ("uses", "dim_customer.status", "dw_table"),
    }


def test_unparsable_sql_is_saved_flagged_with_an_error_and_no_edges(warehouse, model):
    put(warehouse, model, "full", mapping_type="direct", sql_expression="dim_customer.first_name")

    response = put(
        warehouse,
        model,
        "full",
        version=1,
        mapping_type="derived",
        sql_expression="dim_customer.first_name ||",
    )

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["sql_expression"] == "dim_customer.first_name ||"
    assert saved["validation"]["unparsed"] is True
    assert saved["validation"]["errors"][0]["code"] == "unparsed"
    assert saved["inputs"] == []
    assert edges(warehouse, model["full"]["id"]) == []


@pytest.mark.parametrize(
    "sql, field",
    [
        ("dim_customer.nope", "sql_expression"),
        ("no_such_table.first_name", "sql_expression"),
        ("first_name", "sql_expression"),
        ("dim_customer_m.name", "sql_expression"),
    ],
)
def test_inputs_must_exist_in_the_layer_directly_below(warehouse, model, sql, field):
    response = put(warehouse, model, "full", mapping_type="derived", sql_expression=sql)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_mapping"
    assert response.json()["error"]["details"]["field"] == field
    assert get_mapping(warehouse, model["mart"]["id"])["version"] == 0


def test_a_mart_cannot_read_a_staging_or_another_mart_table(warehouse, model):
    engine = warehouse.app.state.engine
    warehouse_id = _warehouse_id(engine)
    staging = _staging_table(engine, warehouse_id, "stg_crm_dbo_customer", ["first_name"])
    assert staging

    response = put(
        warehouse,
        model,
        "full",
        mapping_type="direct",
        sql_expression="stg_crm_dbo_customer.first_name",
    )

    assert response.status_code == 422, response.text


def test_a_core_table_maps_from_staging(warehouse, model):
    engine = warehouse.app.state.engine
    _staging_table(engine, _warehouse_id(engine), "stg_crm_dbo_customer", ["fname"])

    response = warehouse.client("editor").put(
        f"{mapping_path(warehouse, model['core']['id'])}/columns/{model['first']['id']}",
        json={"mapping_type": "direct", "sql_expression": "stg_crm_dbo_customer.fname"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["table_name"] == "stg_crm_dbo_customer"


@pytest.mark.parametrize(
    "body",
    [
        {"mapping_type": "direct", "sql_expression": "UPPER(dim_customer.first_name)"},
        {"mapping_type": "constant", "sql_expression": "dim_customer.first_name"},
        {"mapping_type": "derived", "sql_expression": ""},
        {"mapping_type": "unmapped", "sql_expression": "dim_customer.first_name"},
        {"mapping_type": "lookup", "sql_expression": "1"},
    ],
)
def test_the_mapping_type_must_fit_the_sql(warehouse, model, body):
    response = put(warehouse, model, "full", **body)

    assert response.status_code == 422, response.text


def test_constant_and_unmapped_mappings(warehouse, model):
    constant = put(warehouse, model, "full", mapping_type="constant", sql_expression="'N/A'")
    unmapped = put(warehouse, model, "name", mapping_type="unmapped", rule_text="Not in source")

    assert constant.status_code == 200 and constant.json()["inputs"] == []
    assert unmapped.status_code == 200 and unmapped.json()["sql_expression"] == ""
    assert edges(warehouse, model["full"]["id"]) == []


def test_a_stale_version_is_a_conflict(warehouse, model):
    put(warehouse, model, "full", mapping_type="constant", sql_expression="'x'")

    stale = put(warehouse, model, "full", version=0, mapping_type="constant", sql_expression="'y'")

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_table_level_fields_are_edited_with_a_version(warehouse, model):
    path = mapping_path(warehouse, model["mart"]["id"])

    response = warehouse.client("editor").patch(
        path,
        json={"version": 0, "integration_rule": "Match on email", "match_keys": ["name"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["integration_rule"] == "Match on email"
    assert response.json()["version"] == 1
    stale = warehouse.client("editor").patch(path, json={"version": 0, "notes": "x"})
    assert stale.status_code == 409


def test_a_staging_table_has_no_mapping_here(warehouse, model):
    engine = warehouse.app.state.engine
    staging_id = _staging_table(engine, _warehouse_id(engine), "stg_a_b_c", ["x"])

    response = warehouse.client("viewer").get(mapping_path(warehouse, str(staging_id)))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_mapping"


def test_mappings_are_audited(warehouse, model, clock):
    put(warehouse, model, "full", mapping_type="direct", sql_expression="dim_customer.first_name")
    clock.advance(timedelta(seconds=1))
    put(
        warehouse,
        model,
        "full",
        version=1,
        mapping_type="direct",
        sql_expression="dim_customer.last_name",
    )
    mapping = get_mapping(warehouse, model["mart"]["id"])
    mapping_id = next(c for c in mapping["columns"] if c["column_id"] == model["full"]["id"])["id"]

    trail = AuditService(warehouse.app.state.engine).list(
        warehouse.workspace_id, entity_type="column_mapping", entity_id=uuid.UUID(mapping_id)
    )

    assert [e.old is None for e in trail] == [True, False]
    assert trail[1].old == {"sql_expression": "dim_customer.first_name"}
    assert trail[1].new == {"sql_expression": "dim_customer.last_name"}


def test_lineage_follows_layers_downstream_and_upstream(warehouse, model):
    engine = warehouse.app.state.engine
    _staging_table(engine, _warehouse_id(engine), "stg_crm_dbo_customer", ["fname"])
    warehouse.client("editor").put(
        f"{mapping_path(warehouse, model['core']['id'])}/columns/{model['first']['id']}",
        json={"mapping_type": "direct", "sql_expression": "stg_crm_dbo_customer.fname"},
    )
    put(warehouse, model, "full", mapping_type="direct", sql_expression="dim_customer.first_name")

    up = edges(warehouse, model["full"]["id"], "upstream")
    down = edges(warehouse, model["first"]["id"], "downstream")

    assert {e["from_label"] for e in up} == {
        "dim_customer.first_name",
        "stg_crm_dbo_customer.fname",
    }
    assert {e["to_label"] for e in down} == {"dim_customer_m.full_name"}


def test_a_cycle_in_the_edges_does_not_loop(warehouse, model):
    engine = warehouse.app.state.engine
    a, b = model["first"]["id"], model["last"]["id"]
    with engine.begin() as connection:
        for src, dst in ((a, b), (b, a)):
            connection.execute(
                sa.text(
                    "insert into lineage_edges (id, kind, from_type, from_id, to_type, to_id) "
                    "values (:id, 'value', 'dw_column', :src, 'dw_column', :dst)"
                ),
                {"id": uuid.uuid4(), "src": src, "dst": dst},
            )

    up = edges(warehouse, a, "upstream")
    down = edges(warehouse, a, "downstream")

    assert len(up) == 2 and len(down) == 2


def test_deleting_a_column_removes_the_edges_that_read_it(warehouse, model):
    put(warehouse, model, "full", mapping_type="direct", sql_expression="dim_customer.first_name")

    deleted = warehouse.client("editor").delete(
        f"{base(warehouse)}/tables/{model['core']['id']}/columns/{model['first']['id']}"
    )

    assert deleted.status_code == 204
    assert edges(warehouse, model["full"]["id"]) == []


def test_viewers_read_but_cannot_save(warehouse, model):
    denied = put(warehouse, model, "full", role="viewer", mapping_type="unmapped")

    assert denied.status_code == 403
    assert get_mapping(warehouse, model["mart"]["id"])["version"] == 0


def _warehouse_id(engine: sa.Engine) -> uuid.UUID:
    with engine.connect() as connection:
        return connection.execute(sa.text("select id from data_warehouses limit 1")).scalar_one()


def _staging_table(engine: sa.Engine, warehouse_id: uuid.UUID, name: str, columns: list[str]):
    """A Staging Table, as source analysis will make them (not editable by hand)."""
    table_id, now = uuid.uuid4(), datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into dw_tables (id, data_warehouse_id, layer, name, kind, is_aggregate,"
                " is_conformed, description, created_at, updated_at, version) values (:id, :wh,"
                " 'staging', :name, 'staging', false, false, '', :now, :now, 1)"
            ),
            {"id": table_id, "wh": warehouse_id, "name": name, "now": now},
        )
        for ordinal, column_name in enumerate(columns, start=1):
            connection.execute(
                sa.text(
                    "insert into dw_columns (id, table_id, name, ordinal, data_type, is_nullable,"
                    " role, description, is_system, created_at, updated_at, version) values"
                    " (:id, :t, :name, :o, '{\"type\": \"string\"}', true, 'attribute', '', false,"
                    " :now, :now, 1)"
                ),
                {"id": uuid.uuid4(), "t": table_id, "name": column_name, "o": ordinal, "now": now},
            )
    return table_id


def test_saving_the_same_mapping_again_changes_nothing(warehouse, model):
    body = {"mapping_type": "direct", "sql_expression": "dim_customer.first_name"}
    put(warehouse, model, "full", **body)

    again = put(warehouse, model, "full", version=1, **body)

    assert again.status_code == 200 and again.json()["version"] == 1
