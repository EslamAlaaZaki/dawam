"""The lineage graph and impact report (spec §6.14, stories 79, 110, 111, 112).

One hand-built Workspace with a known graph: source ``crm.dbo.orders`` (amount, status) is
staged, Core ``fact_orders.amount_c`` reads the staged amount and is filtered on the staged
status (a ``uses`` edge), Mart ``mart_orders.amount_m`` reads the Core amount, and the KPI
``Revenue`` links the Mart amount.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.kpis.test_kpi_links import put_links
from tests.kpis.test_kpis import created
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import (
    add_system,
    columns_of,
    generate,
    seed,
    staging_tables,
)

model = test_chat.model  # the assistant's model (a fixture)


def dw(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def table(roles: RoleClients, name: str, layer: str) -> dict:
    body = {"layer": layer, "name": name, "kind": "fact"}
    body |= {"grain": "One row per order", "fact_type": "transactional"}
    response = roles.client("editor").post(f"{dw(roles)}/tables", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def measure(roles: RoleClients, table_id: str, name: str) -> dict:
    body = {
        "name": name,
        "data_type": {"type": "decimal", "precision": 18, "scale": 2},
        "role": "measure",
        "additivity": "additive",
    }
    response = roles.client("editor").post(f"{dw(roles)}/tables/{table_id}/columns", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def map_direct(
    roles: RoleClients, table_id: str, column_id: str, sql: str, version: int | None = None
) -> None:
    body = {"mapping_type": "direct", "sql_expression": sql}
    response = roles.client("editor").put(
        f"{dw(roles)}/tables/{table_id}/mapping/columns/{column_id}",
        json=body | ({"version": version} if version else {}),
    )
    assert response.status_code == 200, response.text


def edge(roles: RoleClients, kind: str, from_type, from_id, to_type, to_id) -> None:
    with roles.app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into lineage_edges (id, kind, from_type, from_id, to_type, to_id)"
                " values (:id, :kind, :ft, :f, :tt, :t)"
            ),
            {
                "id": uuid.uuid4(),
                "kind": kind,
                "ft": from_type,
                "f": from_id,
                "tt": to_type,
                "t": to_id,
            },
        )


@pytest.fixture
def graph(roles: RoleClients) -> dict:
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    source = seed(
        roles,
        add_system(roles),
        "dbo",
        {"orders": [("amount", "numeric(12,2)"), ("status", "varchar(8)")]},
    )
    generate(roles)
    [stg] = staging_tables(roles).values()
    staged = columns_of(roles, stg)
    core = table(roles, "fact_orders", "core")
    amount_c = measure(roles, core["id"], "amount_c")
    map_direct(roles, core["id"], amount_c["id"], f"{stg['name']}.amount")
    edge(roles, "uses", "dw_column", staged["status"]["id"], "dw_table", core["id"])
    mart = table(roles, "mart_orders", "mart")
    amount_m = measure(roles, mart["id"], "amount_m")
    map_direct(roles, mart["id"], amount_m["id"], "fact_orders.amount_c")
    kpi = created(roles, name="Revenue")
    assert put_links(roles, kpi, [amount_m["id"]]).status_code == 200
    return {
        "src_amount": str(source["orders"]["columns"]["amount"]),
        "src_status": str(source["orders"]["columns"]["status"]),
        "stg": stg,
        "stg_amount": staged["amount"]["id"],
        "stg_status": staged["status"]["id"],
        "core": core["id"],
        "amount_c": amount_c["id"],
        "mart": mart["id"],
        "amount_m": amount_m["id"],
        "kpi": kpi["id"],
    }


def lineage(roles: RoleClients, node: str, role="viewer", **params) -> dict:
    return ok(roles.client(role).get(f"{dw(roles)}/lineage", params={"node": node, **params}))


def labels(body: dict) -> set[str]:
    return {n["label"] for n in body["nodes"]}


def pairs(body: dict) -> set[tuple[str, str, str]]:
    by_id = {n["id"]: n["label"] for n in body["nodes"]}
    return {(e["kind"], by_id[e["from_id"]], by_id[e["to_id"]]) for e in body["edges"]}


def test_a_kpi_traces_back_to_its_source_columns(graph, roles):
    stg = graph["stg"]["name"]

    body = lineage(roles, graph["kpi"], direction="upstream")

    assert body["start"] == {"id": graph["kpi"], "type": "kpi", "label": "Revenue", "layer": "kpi"}
    assert pairs(body) == {
        ("kpi", "mart_orders.amount_m", "Revenue"),
        ("value", "fact_orders.amount_c", "mart_orders.amount_m"),
        ("value", f"{stg}.amount", "fact_orders.amount_c"),
        ("uses", f"{stg}.status", "fact_orders"),
        ("value", "crm.dbo.orders.amount", f"{stg}.amount"),
        ("value", "crm.dbo.orders.status", f"{stg}.status"),
    }
    assert [n["layer"] for n in body["nodes"]] == sorted(
        (n["layer"] for n in body["nodes"]),
        key=["source", "staging", "core", "mart", "kpi"].index,
    )


def test_depth_limits_the_walk_and_downstream_reaches_the_kpi(graph, roles):
    one = lineage(roles, graph["amount_c"], direction="upstream", depth=1)
    down = lineage(roles, graph["src_amount"], direction="downstream")

    assert labels(one) == {
        "fact_orders.amount_c",
        f"{graph['stg']['name']}.amount",
        f"{graph['stg']['name']}.status",
        "fact_orders",
    }
    assert {"crm.dbo.orders.amount", "mart_orders.amount_m", "Revenue"} <= labels(down)
    assert "crm.dbo.orders.status" not in labels(down)


def test_both_directions_from_a_middle_node(graph, roles):
    body = lineage(roles, graph["amount_c"], depth=1)

    assert labels(body) >= {"mart_orders.amount_m", f"{graph['stg']['name']}.amount"}
    assert "Revenue" not in labels(body)


def test_the_impact_of_a_source_column_lists_dw_columns_and_kpis(graph, roles):
    amount = ok(
        roles.client("viewer").get(f"{dw(roles)}/impact", params={"node": graph["src_amount"]})
    )
    status = ok(
        roles.client("viewer").get(f"{dw(roles)}/impact", params={"node": graph["src_status"]})
    )

    stg = graph["stg"]["name"]
    assert [c["label"] for c in amount["columns"]] == [
        f"{stg}.amount",
        "fact_orders.amount_c",
        "mart_orders.amount_m",
    ]
    assert amount["tables"] == []
    assert [k["label"] for k in amount["kpis"]] == ["Revenue"]
    # A filter column steers the whole table it filters, and so everything downstream.
    assert [t["label"] for t in status["tables"]] == ["fact_orders"]
    assert "mart_orders.amount_m" in [c["label"] for c in status["columns"]]
    assert [k["label"] for k in status["kpis"]] == ["Revenue"]


def test_a_cycle_terminates(graph, roles):
    edge(roles, "value", "dw_column", graph["amount_m"], "dw_column", graph["amount_c"])

    body = lineage(roles, graph["amount_c"])

    assert ("value", "mart_orders.amount_m", "fact_orders.amount_c") in pairs(body)


def test_an_unknown_node_is_not_found(graph, roles):
    response = roles.client("viewer").get(
        f"{dw(roles)}/lineage", params={"node": str(uuid.uuid4())}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_500_node_graph_is_served_quickly(graph, roles):
    engine = roles.app.state.engine
    warehouse_id = engine.connect().execute(sa.text("select id from data_warehouses")).scalar()
    now = datetime.now(UTC)
    tables = [uuid.uuid4(), uuid.uuid4()]
    cols = [[uuid.uuid4() for _ in range(250)] for _ in tables]
    with engine.begin() as connection:
        for n, table_id in enumerate(tables):
            connection.execute(
                sa.text(
                    "insert into dw_tables (id, data_warehouse_id, layer, name, kind,"
                    " is_aggregate, is_conformed, description, created_at, updated_at, version)"
                    " values (:id, :wh, 'core', :name, 'dimension', false, false, '', :now,"
                    " :now, 1)"
                ),
                {"id": table_id, "wh": warehouse_id, "name": f"wide_{n}", "now": now},
            )
            connection.execute(
                sa.text(
                    "insert into dw_columns (id, table_id, name, ordinal, data_type, is_nullable,"
                    " role, description, is_system, created_at, updated_at, version) values"
                    " (:id, :t, :name, :o, '{\"type\": \"string\"}', true, 'attribute', '',"
                    " false, :now, :now, 1)"
                ),
                [
                    {"id": c, "t": table_id, "name": f"c{i}", "o": i, "now": now}
                    for i, c in enumerate(cols[n])
                ],
            )
        connection.execute(
            sa.text(
                "insert into lineage_edges (id, kind, from_type, from_id, to_type, to_id)"
                " values (:id, 'value', 'dw_column', :f, :tt, :t)"
            ),
            [
                {"id": uuid.uuid4(), "f": a, "tt": "dw_column", "t": b}
                for a, b in zip(cols[0], cols[1], strict=True)
            ]
            + [{"id": uuid.uuid4(), "f": b, "tt": "kpi", "t": graph["kpi"]} for b in cols[1]],
        )
    lineage(roles, graph["kpi"], direction="upstream")  # warm

    started = time.perf_counter()
    body = lineage(roles, graph["kpi"], direction="upstream")

    assert len(body["nodes"]) > 500
    assert time.perf_counter() - started < 2


def test_the_assistant_reads_a_kpis_lineage(graph, roles, model, fake_llm):
    status, seen = use(roles, fake_llm, "get_lineage", node_id=graph["kpi"])

    assert status == "ok"
    result = payload(seen)
    assert result["start"] == "Revenue (kpi)"
    assert {
        "kind": "value",
        "from": "crm.dbo.orders.amount (source)",
        "to": f"{graph['stg']['name']}.amount (staging)",
    } in result["edges"]
    assert result["nodes_left_out"] == 0


def confirm_pii(roles: RoleClients, column_id: str) -> None:
    with roles.app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into pii_findings (id, src_column_id, rule, category, confidence,"
                " evidence, status, detected_at) values (:id, :c, 'national_id',"
                " 'direct_identifier', 0.9, 'name', 'confirmed', :now)"
            ),
            {"id": uuid.uuid4(), "c": column_id, "now": datetime.now(UTC)},
        )


def pii_view(roles: RoleClients, role="viewer") -> dict:
    return ok(roles.client(role).get(f"{dw(roles)}/pii"))


def test_pii_flows_through_value_edges_to_columns(graph, roles):
    confirm_pii(roles, graph["src_amount"])

    body = pii_view(roles)

    assert [c["label"] for c in body["columns"]] == [
        f"{graph['stg']['name']}.amount",
        "fact_orders.amount_c",
        "mart_orders.amount_m",
    ]
    assert body["tables"] == []


def test_join_only_use_marks_the_table_not_its_columns(graph, roles):
    confirm_pii(roles, graph["src_status"])

    body = pii_view(roles)

    # The staged status column is derived (a direct copy); the Core table it filters is
    # only influenced, and its measure, fed by the amount, is not derived.
    assert [c["label"] for c in body["columns"]] == [f"{graph['stg']['name']}.status"]
    assert [t["label"] for t in body["tables"]] == ["fact_orders"]


def test_the_view_follows_mapping_changes_and_ignores_unconfirmed_findings(graph, roles):
    assert pii_view(roles) == {"columns": [], "tables": []}
    confirm_pii(roles, graph["src_amount"])
    map_direct(roles, graph["core"], graph["amount_c"], f"{graph['stg']['name']}.status", version=1)

    labels_now = [c["label"] for c in pii_view(roles)["columns"]]

    assert "fact_orders.amount_c" not in labels_now
    assert labels_now == [f"{graph['stg']['name']}.amount"]
