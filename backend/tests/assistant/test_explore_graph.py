"""The assistant's ``explore_graph`` tool (#379): one object's neighbours across lineage,
relationships, KPI links and the documents that mention it, at most two hops away.

The Workspace is the lineage tests' hand-built graph (``crm.dbo.orders`` staged, Core
``fact_orders.amount_c``, Mart ``mart_orders.amount_m`` and the KPI ``Revenue``), plus a
``crm.dbo.payments`` table whose ``amount`` has a suggested relationship to the orders'
amount. The model is the scripted fake provider, so the tests see what the tool put in
front of it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from tests.assistant import test_chat
from tests.assistant.test_source_tools import level, payload, upload, use
from tests.lineage import test_lineage
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import seed

model = test_chat.model  # the assistant's model (a fixture)
graph = test_lineage.graph  # the hand-built lineage graph (a fixture)


def relate(roles: RoleClients, from_id: str, to_id: str, status: str = "suggested") -> None:
    with roles.app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into relationships (id, from_column_id, to_column_id, origin,"
                " confidence, evidence, status, version, detected_at)"
                " values (:id, :f, :t, 'inferred', 0.9, '{}', :s, 1, :now)"
            ),
            {"id": uuid.uuid4(), "f": from_id, "t": to_id, "s": status, "now": datetime.now(UTC)},
        )


@pytest.fixture
def world(roles: RoleClients, graph: dict) -> dict:
    with roles.app.state.engine.connect() as connection:
        system = connection.scalar(
            sa.text(
                "select s.source_system_id from src_columns c"
                " join src_tables t on t.id = c.table_id"
                " join src_db_schemas s on s.id = t.db_schema_id where c.id = :c"
            ),
            {"c": graph["src_amount"]},
        )
    payments = seed(roles, system, "dbo", {"payments": [("amount", "numeric(12,2)")]})
    pay_amount = str(payments["payments"]["columns"]["amount"])
    relate(roles, pay_amount, graph["src_amount"])
    return {**graph, "system": str(system), "pay_amount": pay_amount}


def labels(result: dict) -> set[str]:
    return {n["label"] for n in result["nodes"]}


def test_a_kpi_reaches_its_linked_columns_and_what_feeds_them_within_two_hops(
    roles, model, fake_llm, world
):
    status, seen = use(roles, fake_llm, "explore_graph", object_id=world["kpi"], depth=2)

    assert status == "ok"
    result = payload(seen)
    assert result["start"]["label"] == "Revenue" and result["depth"] == 2
    assert labels(result) == {"Revenue", "mart_orders.amount_m", "fact_orders.amount_c"}
    distances = {n["label"]: n["distance"] for n in result["nodes"]}
    assert distances == {"Revenue": 0, "mart_orders.amount_m": 1, "fact_orders.amount_c": 2}
    assert {"kind": "kpi", "from": "mart_orders.amount_m (mart)", "to": "Revenue (kpi)"} in result[
        "edges"
    ]
    assert result["nodes_left_out"] == 0


def test_depth_is_capped_at_two(roles, model, fake_llm, world):
    status, seen = use(roles, fake_llm, "explore_graph", object_id=world["kpi"], depth=3)

    assert status == "error" and "depth" in seen
    one = payload(use(roles, fake_llm, "explore_graph", object_id=world["kpi"])[1])
    assert one["depth"] == 1
    assert labels(one) == {"Revenue", "mart_orders.amount_m"}


def test_relationships_and_lineage_combine_across_hops(roles, model, fake_llm, world):
    status, seen = use(
        roles,
        fake_llm,
        "explore_graph",
        object_id=world["pay_amount"],
        system_id=world["system"],
        depth=2,
    )

    assert status == "ok"
    result = payload(seen)
    stg = world["stg"]["name"]
    assert labels(result) == {"crm.dbo.payments.amount", "crm.dbo.orders.amount", f"{stg}.amount"}
    [related] = [e for e in result["edges"] if e["kind"] == "relationship"]
    assert related["from"] == "crm.dbo.payments.amount (source)"
    assert related["to"] == "crm.dbo.orders.amount (source)"
    assert related["status"] == "suggested" and related["confidence"] == 0.9


def test_rejected_relationships_are_not_neighbours_and_without_a_system_none_are_read(
    roles, model, fake_llm, world
):
    other = seed(roles, uuid.UUID(world["system"]), "dbo", {"refunds": [("amount", "int")]})
    relate(roles, str(other["refunds"]["columns"]["amount"]), world["pay_amount"], "rejected")

    _, seen = use(
        roles, fake_llm, "explore_graph", object_id=world["pay_amount"], system_id=world["system"]
    )
    _, unscoped = use(roles, fake_llm, "explore_graph", object_id=world["pay_amount"])

    assert labels(payload(seen)) == {"crm.dbo.payments.amount", "crm.dbo.orders.amount"}
    result = payload(unscoped)
    assert labels(result) == {"crm.dbo.payments.amount"}
    assert "system_id" in result["note"]


def test_documents_that_mention_the_object_come_only_at_the_documents_level(
    roles, model, fake_llm, world
):
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{world['system']}"
    file_id = upload(roles, system, "kpis.md", "# Revenue\nRevenue is the sum of order amounts.")

    _, withheld = use(roles, fake_llm, "explore_graph", object_id=world["kpi"])
    level(roles, "documents")
    _, shown = use(roles, fake_llm, "explore_graph", object_id=world["kpi"])

    hidden = payload(withheld)
    assert hidden["documents"] == [] and "documents" in hidden["documents_note"]
    assert "sum of order amounts" not in withheld
    [document] = payload(shown)["documents"]
    assert document["file_id"] == file_id and document["document"] == "kpis.md"
    assert "sum of order amounts" in document["text"] and file_id in document["link"]


def test_an_unknown_object_is_not_found(roles, model, fake_llm, world):
    status, seen = use(roles, fake_llm, "explore_graph", object_id=str(uuid.uuid4()))

    assert status == "error" and "not_found" in seen
