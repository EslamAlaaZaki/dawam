"""Staging generation (spec §6.7, stories 81-85): the Staging Layer is made by software from
the Source Schema, through the HTTP API. The Source Schema is seeded straight into the
sources module's tables (extraction itself is covered elsewhere)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from dawam.modules.warehouse.staging_naming import stable_hash
from tests.roles import RoleClients


def base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def systems(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/systems"


@pytest.fixture
def warehouse(roles: RoleClients) -> RoleClients:
    response = roles.client("editor").post(base(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    return roles


def add_system(roles: RoleClients, code: str = "crm") -> uuid.UUID:
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": code, "code": code}
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def seed(
    roles: RoleClients,
    system_id: uuid.UUID,
    schema: str,
    tables: dict[str, list[tuple[str, str]]],
    *,
    kind: str = "table",
    engine: str | None = None,
    status: str = "present",
) -> dict[str, dict]:
    """Source Objects of ``schema``: ``{table: [(column, data type)]}``. Returns the ids."""
    db = roles.app.state.engine
    now = datetime.now(UTC)
    out: dict[str, dict] = {}
    with db.begin() as connection:
        schema_id = connection.scalar(
            sa.text("select id from src_db_schemas where source_system_id = :s and name = :n"),
            {"s": system_id, "n": schema},
        )
        if schema_id is None:
            schema_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "insert into src_db_schemas (id, source_system_id, name, status)"
                    " values (:id, :s, :n, 'present')"
                ),
                {"id": schema_id, "s": system_id, "n": schema},
            )
        table_rows, column_rows = [], []
        for name, columns in tables.items():
            table_id = uuid.uuid4()
            table_rows.append(
                {"id": table_id, "schema": schema_id, "n": name, "k": kind, "st": status}
            )
            column_ids = {column_name: uuid.uuid4() for column_name, _ in columns}
            column_rows += [
                {
                    "id": column_ids[column_name],
                    "t": table_id,
                    "n": column_name,
                    "d": f'{{"data_type": "{data_type}", "is_nullable": true}}',
                }
                for column_name, data_type in columns
            ]
            out[name] = {"id": table_id, "columns": column_ids}
        if table_rows:
            connection.execute(
                sa.text(
                    "insert into src_tables (id, db_schema_id, name, kind, current_definition,"
                    " status, version) values (:id, :schema, :n, :k, '{}', :st, 1)"
                ),
                table_rows,
            )
        if column_rows:
            connection.execute(
                sa.text(
                    "insert into src_columns (id, table_id, name, current_definition, status,"
                    " version) values (:id, :t, :n, cast(:d as json), 'present', 1)"
                ),
                column_rows,
            )
        if engine:
            connection.execute(
                sa.text(
                    "insert into connections (id, source_system_id, engine, host, port, database,"
                    " username, options, allowed_schemas, created_at, updated_at) values"
                    " (:id, :s, :e, 'h', 1, 'd', 'u', '{}', '{}', :now, :now)"
                    " on conflict (source_system_id) do nothing"
                ),
                {"id": uuid.uuid4(), "s": system_id, "e": engine, "now": now},
            )
    return out


def generate(roles: RoleClients, role: str = "editor"):
    return roles.client(role).post(f"{base(roles)}/staging/generate")


def staging_tables(roles: RoleClients) -> dict[str, dict]:
    response = roles.client("viewer").get(f"{base(roles)}/tables", params={"layer": "staging"})
    assert response.status_code == 200, response.text
    return {t["name"]: t for t in response.json()["items"]}


def columns_of(roles: RoleClients, table: dict) -> dict[str, dict]:
    response = roles.client("viewer").get(f"{base(roles)}/tables/{table['id']}")
    assert response.status_code == 200, response.text
    return {c["name"]: c for c in response.json()["columns"]}


def query(roles: RoleClients, sql: str, **params):
    with roles.app.state.engine.connect() as connection:
        return connection.execute(sa.text(sql), params).all()


def test_generating_before_the_warehouse_is_set_up_is_refused(roles: RoleClients):
    add_system(roles)

    response = generate(roles)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_set_up"


def test_a_viewer_cannot_generate(warehouse: RoleClients):
    assert generate(warehouse, "viewer").status_code == 403


def test_a_table_becomes_a_staging_table_with_audit_columns(warehouse: RoleClients):
    system = add_system(warehouse, "crm")
    seed(
        warehouse,
        system,
        "dbo",
        {"Customers": [("Code", "integer"), ("Full Name", "character varying(80)")]},
    )

    response = generate(warehouse)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["tables_created"], body["columns_created"], body["flags"]) == (1, 4, [])
    [table] = staging_tables(warehouse).values()
    assert table["name"] == "stg_crm_dbo_customers"
    assert table["layer"] == "staging"
    columns = columns_of(warehouse, table)
    assert list(columns) == ["code", "full_name", "load_ts", "source_system"]
    assert columns["full_name"]["data_type"] == {
        "type": "string",
        "length": 80,
        "precision": None,
        "scale": None,
    }
    assert columns["load_ts"]["role"] == "audit" and columns["load_ts"]["is_nullable"] is False
    assert columns["source_system"]["data_type"]["type"] == "string"


def test_every_source_column_gets_a_direct_mapping_and_a_lineage_edge(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = seed(warehouse, system, "dbo", {"orders": [("amount", "numeric(12,2)")]})
    generate(warehouse)

    rows = query(
        warehouse,
        "select m.mapping_type, m.sql_expression, e.kind, e.from_type, e.from_id, c.name"
        " from column_mappings m join dw_columns c on c.id = m.dw_column_id"
        " left join lineage_edges e on e.mapping_id = m.id order by c.ordinal",
    )

    amount, load_ts, source_system = rows
    assert (amount.mapping_type, amount.sql_expression) == ("direct", "dbo.orders.amount")
    assert (amount.kind, amount.from_type) == ("value", "src_column")
    assert amount.from_id == ids["orders"]["columns"]["amount"]
    assert (load_ts.mapping_type, load_ts.kind) == ("system", None)
    assert source_system.sql_expression == "'crm'"


def test_the_lineage_of_a_staging_column_reaches_its_source_column(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = seed(warehouse, system, "dbo", {"orders": [("amount", "integer")]})
    generate(warehouse)
    [table] = staging_tables(warehouse).values()
    amount = columns_of(warehouse, table)["amount"]

    response = warehouse.client("viewer").get(
        f"{base(warehouse)}/lineage/columns/{amount['id']}", params={"direction": "upstream"}
    )

    assert response.status_code == 200, response.text
    [edge] = response.json()["edges"]
    assert edge["from_type"] == "src_column"
    assert edge["from_id"] == str(ids["orders"]["columns"]["amount"])


def test_views_are_staged_only_when_opted_in(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"orders": [("id", "integer")]})
    view = seed(warehouse, system, "dbo", {"v_orders": [("id", "integer")]}, kind="view")
    first = generate(warehouse).json()

    path = f"{systems(warehouse)}/{system}/tables/{view['v_orders']['id']}"
    optin = warehouse.client("editor").patch(
        path, json={"version": 1, "include_view_in_staging": True}
    )
    second = generate(warehouse).json()

    assert first["tables_created"] == 1
    assert optin.status_code == 200 and optin.json()["include_view_in_staging"] is True
    assert (second["tables_created"], second["tables_existing"]) == (1, 1)
    assert set(staging_tables(warehouse)) == {"stg_crm_dbo_orders", "stg_crm_dbo_v_orders"}


def test_only_a_view_can_be_opted_in(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = seed(warehouse, system, "dbo", {"orders": [("id", "integer")]})
    path = f"{systems(warehouse)}/{system}/tables/{ids['orders']['id']}"

    response = warehouse.client("editor").patch(
        path, json={"version": 1, "include_view_in_staging": True}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_enhancement"


def test_generating_again_changes_nothing(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"a": [("x", "integer")], "b": [("y", "integer")]})
    generate(warehouse)
    before = (
        query(warehouse, "select count(*) from dw_tables"),
        query(warehouse, "select count(*) from lineage_edges"),
    )

    again = generate(warehouse).json()

    assert (again["tables_created"], again["columns_created"], again["tables_existing"]) == (
        0,
        0,
        2,
    )
    assert (
        query(warehouse, "select count(*) from dw_tables"),
        query(warehouse, "select count(*) from lineage_edges"),
    ) == before


def test_removed_and_deleted_source_tables_are_not_staged(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"kept": [("x", "integer")]})
    seed(warehouse, system, "dbo", {"gone": [("x", "integer")]}, status="source_removed")

    generate(warehouse)

    assert set(staging_tables(warehouse)) == {"stg_crm_dbo_kept"}


def test_non_latin_names_get_placeholders_stored_once_on_the_source_object(
    warehouse: RoleClients,
):
    system = add_system(warehouse)
    ids = seed(
        warehouse, system, "dbo", {"客户": [("名前", "integer"), ("年齢", "integer")], "a": []}
    )

    body = generate(warehouse).json()

    names = staging_tables(warehouse)
    assert "stg_crm_dbo_tbl_001" in names
    assert sorted(columns_of(warehouse, names["stg_crm_dbo_tbl_001"]))[:2] == ["col_001", "col_002"]
    assert {f["code"] for f in body["flags"]} == {"placeholder"}
    assert len(body["flags"]) == 3
    stored = query(
        warehouse,
        "select placeholder_no from src_tables where id = :id",
        id=ids["客户"]["id"],
    )
    assert stored[0][0] == 1
    # A table added later is numbered after it; the first keeps its number.
    later = seed(warehouse, system, "dbo", {"表": [("x", "integer")]})
    generate(warehouse)
    assert "stg_crm_dbo_tbl_002" in staging_tables(warehouse)
    assert (
        query(
            warehouse, "select placeholder_no from src_tables where id = :id", id=ids["客户"]["id"]
        )[0][0]
        == 1
    )
    assert later


def test_colliding_names_get_the_hash_and_are_flagged(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = seed(
        warehouse,
        system,
        "dbo",
        {"Order Items": [("id", "integer")], "order_items": [("id", "integer")]},
    )

    body = generate(warehouse).json()

    names = set(staging_tables(warehouse))
    expected = {
        f"stg_crm_dbo_order_items_{stable_hash(str(ids[t]['id']))}"
        for t in ("Order Items", "order_items")
    }
    assert names == expected
    assert {f["code"] for f in body["flags"]} == {"collision"}


def test_a_name_over_the_platform_limit_is_truncated_with_the_hash(warehouse: RoleClients):
    system = add_system(warehouse)
    ids = seed(warehouse, system, "dbo", {"t" * 100: [("c", "integer")]})

    body = generate(warehouse).json()

    [name] = staging_tables(warehouse)
    assert len(name) == 63
    assert name.endswith(stable_hash(str(next(iter(ids.values()))["id"])))
    assert [f["code"] for f in body["flags"]] == ["truncated"]


def test_lossy_and_unknown_types_are_flagged_on_the_column(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(
        warehouse,
        system,
        "dbo",
        {"t": [("n", "NUMBER"), ("x", "XMLTYPE"), ("ok", "integer")]},
        engine="oracle",
    )

    body = generate(warehouse).json()

    by_column = {f["column_name"]: f["code"] for f in body["flags"]}
    assert by_column == {"n": "lossy_type", "x": "fallback_type"}
    [table] = staging_tables(warehouse).values()
    columns = columns_of(warehouse, table)
    assert columns["n"]["data_type"]["precision"] == 38
    assert columns["x"]["data_type"]["type"] == "text"


def test_a_column_named_like_an_audit_column_is_renamed(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"t": [("LOAD_TS", "timestamp"), ("order", "integer")]})

    generate(warehouse)

    [table] = staging_tables(warehouse).values()
    names = list(columns_of(warehouse, table))
    assert names[1] == "order_col"
    assert names[0].startswith("load_ts_") and names[2] == "load_ts"


def test_the_audit_columns_are_configurable(warehouse: RoleClients):
    response = warehouse.client("editor").patch(
        base(warehouse),
        json={
            "version": 1,
            "naming_rules": {"load_ts_column": "etl_loaded_at", "source_system_column": "src"},
        },
    )
    assert response.status_code == 200, response.text
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"t": [("x", "integer")]})

    generate(warehouse)

    [table] = staging_tables(warehouse).values()
    assert list(columns_of(warehouse, table)) == ["x", "etl_loaded_at", "src"]


@pytest.mark.parametrize(
    "rules",
    [
        {"load_ts_column": "bad name"},
        {"load_ts_column": "order"},
        {"load_ts_column": "same", "source_system_column": "SAME"},
    ],
)
def test_audit_column_names_are_validated(warehouse: RoleClients, rules):
    response = warehouse.client("editor").patch(
        base(warehouse), json={"version": 1, "naming_rules": rules}
    )

    assert response.status_code == 422, response.text


def test_two_systems_are_staged_side_by_side(warehouse: RoleClients):
    seed(warehouse, add_system(warehouse, "crm"), "dbo", {"customer": [("id", "integer")]})
    seed(warehouse, add_system(warehouse, "erp"), "dbo", {"customer": [("id", "integer")]})

    generate(warehouse)

    assert set(staging_tables(warehouse)) == {"stg_crm_dbo_customer", "stg_erp_dbo_customer"}


def test_generation_is_audited_once(warehouse: RoleClients):
    system = add_system(warehouse)
    seed(warehouse, system, "dbo", {"a": [("x", "integer")], "b": [("x", "integer")]})

    generate(warehouse)

    rows = query(
        warehouse,
        "select new from audit_entries where entity_type = 'data_warehouse'"
        " and new::text like '%staging_generated%'",
    )
    assert len(rows) == 1 and rows[0][0]["staging_generated"]["tables"] == 2


def test_two_thousand_tables_generate_in_under_thirty_seconds(warehouse: RoleClients):
    system = add_system(warehouse)
    for chunk in range(20):
        seed(
            warehouse,
            system,
            "dbo",
            {
                f"table_{chunk}_{n}": [
                    ("id", "integer"),
                    ("name", "varchar(50)"),
                    ("amount", "numeric(12,2)"),
                    ("created", "timestamp"),
                    ("note", "text"),
                ]
                for n in range(100)
            },
        )

    started = time.perf_counter()
    response = generate(warehouse)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    assert response.json()["tables_created"] == 2000
    assert elapsed < 30, f"took {elapsed:.1f}s"
