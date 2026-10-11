"""AI documentation drafts for a Source System (spec §6.8, story 70).

The model drafts descriptions (including what codes and statuses mean), classifications and
SCD hints, and accepts or rejects inferred relationships, all through ``propose_changes``:
the drafts arrive as one ``ai`` Change Set against the Source Schema enhancements. A
relationship decision may cite the run's COUNT queries, whose counts (never values) become
its evidence. Model-written text is redacted with the PII validators before it is saved, and
the drafting works at every data-sharing level. The source is a scratch PostgreSQL database
where ``orders.customer_id`` and ``invoices.customer_id`` are undeclared relationships to
``customers.id`` (only the first holds matching values).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dawam.modules.audit import AuditService
from dawam.modules.llm import FakeAdapter, Reply
from dawam.platform.config import Settings
from tests.assistant import test_chat
from tests.assistant.test_chat import ask, conversation, tool, tool_names
from tests.assistant.test_source_tools import level, payload, system_id, use
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_relationship_inference import SOURCE, by_link, listed, run
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source
model = test_chat.model

IQAMA = "2000000006"
EMAIL = "zz-sentinel@leak.example"
ORDERS = ("public.orders.customer_id", "public.customers.id")
INVOICES = ("public.invoices.customer_id", "public.customers.id")
OVERLAP = (
    "SELECT COUNT(*) AS total, COUNT(c.id) AS matched FROM public.orders o "
    "LEFT JOIN public.customers c ON o.customer_id = c.id"
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"assistant_max_tool_calls": 10})


@pytest.fixture
def source(roles: RoleClients, scratch_source) -> str:
    """An extracted system with its relationships inferred; the Workspace shares metadata."""
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    run(roles, system)
    level(roles, "metadata")
    return system


def relationship(roles: RoleClients, system: str, pair: tuple[str, str]) -> dict[str, Any]:
    return by_link(listed(roles, system, min_confidence=0))[pair]


def chat(
    roles: RoleClients, fake_llm: FakeAdapter, *calls: Any, as_role: str = "editor"
) -> list[tuple[str, str]]:
    """Have the model make each tool call in turn; return (status, what it then saw)."""
    fake_llm.calls.clear()
    fake_llm.script(*(Reply(tool_calls=(c,)) for c in calls), Reply(text="done"))
    client = roles.client(as_role)
    events = ask(client, roles, conversation(client, roles), "Document this system")
    statuses = [shown["status"] for kind, shown in events if kind == "tool"]
    seen = [fake_llm.calls[i + 1].messages[-1].content or "" for i in range(len(calls))]
    return list(zip(statuses, seen, strict=True))


def propose(system: str, call_id: str = "p", **arguments: Any):
    arguments = {"title": "Document the orders", **arguments}
    return tool("propose_changes", call_id, source_system_id=system_id(system), **arguments)


def decide(rel: dict[str, Any], decision: str, *queries: int, reason: str = "Names match"):
    return {
        "relationship_id": rel["id"],
        "decision": decision,
        "reason": reason,
        "evidence_queries": list(queries),
        "label": "orders -> customers",
    }


def only_change_set(roles: RoleClients, as_role: str = "editor") -> dict[str, Any]:
    """The one Change Set; one proposed in a chat is listed to the chat's member."""
    base = f"/api/v1/workspaces/{roles.workspace_id}/change-sets"
    client = roles.client(as_role)
    [listed_set] = client.get(base).json()["items"]
    return client.get(f"{base}/{listed_set['id']}").json()


def accept(roles: RoleClients, change_set: dict[str, Any]) -> dict[str, Any]:
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/"
        f"{change_set['change_set']['id']}/accept",
        json={},
    )
    assert response.status_code == 200, response.text
    return response.json()


# -- drafts as a Change Set ----------------------------------------------------------------


def test_drafts_arrive_as_an_ai_change_set_with_model_text_redacted(roles, model, fake_llm, source):
    tables = {t["name"]: t for t in schema(roles, source)["tables"]}
    orders = tables["orders"]
    [quantity] = [c for c in orders["columns"] if c["name"] == "quantity"]

    [(status, seen)] = chat(
        roles,
        fake_llm,
        propose(
            source,
            title=f"Drafts (asked by {EMAIL})",
            items=[
                {
                    "key": "orders",
                    "object_type": "source_table",
                    "object_id": orders["id"],
                    "changes": {
                        "description": f"Customer orders, e.g. the one for Iqama {IQAMA}.",
                        "classification": "transactional",
                        "scd_hint": f"Append-only; contact {EMAIL} for corrections.",
                        "tags": ["sales", f"owner {EMAIL}"],
                    },
                    "label": "public.orders",
                },
                {
                    "key": "quantity",
                    "object_type": "source_column",
                    "object_id": quantity["id"],
                    "changes": {"description": "Units ordered; 0 means a cancelled line."},
                    "depends_on": ["orders"],
                },
            ],
        ),
    )

    assert status == "ok", seen
    detail = only_change_set(roles)
    assert detail["change_set"]["origin"] == "ai"
    assert detail["change_set"]["scope"] == {
        "kind": "source_enhancements",
        "source_system_id": system_id(source),
    }
    stored = json.dumps(detail)
    assert IQAMA not in stored and EMAIL not in stored
    table_item, column_item = detail["items"]
    assert table_item["payload"]["classification"] == "transactional"
    assert "[redacted: " in table_item["payload"]["description"]
    assert "[redacted: " in table_item["payload"]["scd_hint"]
    assert table_item["payload"]["tags"][0] == "sales"
    assert column_item["payload"] == {"description": "Units ordered; 0 means a cancelled line."}
    assert "[redacted: " in detail["change_set"]["title"]

    accept(roles, detail)

    documented = {t["name"]: t for t in schema(roles, source)["tables"]}["orders"]
    assert documented["classification"] == "transactional"
    assert documented["description"].startswith("Customer orders, e.g. the one for Iqama [")


# -- relationships -------------------------------------------------------------------------


def test_get_relationships_lists_the_suggested_ones_at_the_metadata_level(
    roles, model, fake_llm, source
):
    status, seen = use(roles, fake_llm, "get_relationships", system_id=system_id(source))

    assert status == "ok"
    found = {(r["from"]["name"], r["to"]["name"]): r for r in payload(seen)["relationships"]}
    orders = found[ORDERS]
    assert orders["id"] == relationship(roles, source, ORDERS)["id"]
    assert orders["status"] == "suggested" and orders["confidence"] >= 0.6
    assert "signals" in orders["evidence"] and orders["from"]["link"]


def test_a_confirmation_carries_the_counts_of_the_queries_it_cites(
    roles, model, fake_llm, source, app
):
    level(roles, "samples")
    orders = relationship(roles, source, ORDERS)

    (query_status, query_seen), (status, seen) = chat(
        roles,
        fake_llm,
        tool("run_source_query", "q", system_id=system_id(source), sql=OVERLAP),
        propose(
            source,
            relationships=[decide(orders, "accept", 1, reason="Every order's customer exists")],
        ),
    )

    assert query_status == "ok" and payload(query_seen)["query_ref"] == 1
    assert status == "ok", seen
    detail = only_change_set(roles)
    [item] = detail["items"]
    assert item["object_type"] == "source_relationship" and item["object_id"] == orders["id"]
    assert item["base_values"] == {"status": "suggested", "ai_evidence": None}
    assert item["payload"] == {
        "status": "accepted",
        "ai_evidence": {
            "reason": "Every order's customer exists",
            "queries": [{"sql": OVERLAP, "counts": {"total": 30, "matched": 30}}],
        },
    }

    accept(roles, detail)

    after = relationship(roles, source, ORDERS)
    assert after["status"] == "accepted" and after["version"] == orders["version"] + 1
    assert after["evidence"]["ai"] == item["payload"]["ai_evidence"]
    assert after["evidence"]["signals"] == orders["evidence"]["signals"], "rules' evidence stays"
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="relationship", entity_id=orders["id"]
    )
    assert entry.via == "ai" and entry.new["status"] == "accepted"


def test_only_the_count_columns_of_a_one_row_result_can_be_cited(roles, model, fake_llm, source):
    level(roles, "samples")
    orders = relationship(roles, source, ORDERS)
    sid = system_id(source)

    (rows, rows_seen), (mixed, mixed_seen), (bad, bad_seen), (good, good_seen) = chat(
        roles,
        fake_llm,
        tool("run_source_query", "q1", system_id=sid, sql="SELECT o.sku FROM public.orders o"),
        tool(
            "run_source_query",
            "q2",
            system_id=sid,
            sql="SELECT COUNT(*) AS n, MAX(o.quantity) AS top FROM public.orders o",
        ),
        propose(source, "p1", relationships=[decide(orders, "accept", 2)]),
        propose(source, "p2", relationships=[decide(orders, "accept", 1)]),
    )

    assert (rows, mixed) == ("ok", "ok")
    assert "query_ref" not in payload(rows_seen), "rows are not counts"
    assert payload(mixed_seen)["query_ref"] == 1
    assert bad == "error" and "unknown_query_ref" in bad_seen
    assert good == "ok", good_seen
    [item] = only_change_set(roles)["items"]
    [cited] = item["payload"]["ai_evidence"]["queries"]
    assert cited["counts"] == {"n": 30}, "the MAX is a value, not a count"


def test_a_decision_works_without_sample_access(roles, model, fake_llm, source):
    invoices = relationship(roles, source, INVOICES)

    [(status, seen)] = chat(
        roles,
        fake_llm,
        propose(
            source,
            relationships=[
                decide(invoices, "reject", reason="The documents say invoices bill accounts.")
            ],
        ),
    )

    assert status == "ok", seen
    assert "run_source_query" not in tool_names(fake_llm)
    detail = only_change_set(roles)
    assert detail["items"][0]["payload"]["ai_evidence"] == {
        "reason": "The documents say invoices bill accounts."
    }
    accept(roles, detail)
    assert relationship(roles, source, INVOICES)["status"] == "rejected"


def test_a_relationship_outside_the_named_source_system_or_a_bad_proposal_is_refused(
    roles, model, fake_llm, source
):
    orders = relationship(roles, source, ORDERS)
    created = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Other", "code": "oth"}
    )
    other = created.json()["id"]

    results = chat(
        roles,
        fake_llm,
        tool(
            "propose_changes",
            "p1",
            title="x",
            source_system_id=other,
            relationships=[decide(orders, "accept")],
        ),
        propose(source, "p2"),  # neither items nor relationships
        propose(source, "p3", relationships=[decide(orders, "accept", 7)]),
        as_role="owner",
    )

    assert [status for status, _ in results] == ["error", "error", "error"]
    assert "outside" in results[0][1] and "nothing_proposed" in results[1][1]
    assert "unknown_query_ref" in results[2][1]
    listing = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/change-sets")
    assert listing.json()["items"] == []


def test_a_viewer_cannot_propose_documentation(roles, model, fake_llm, source):
    orders = relationship(roles, source, ORDERS)

    [(status, _)] = chat(
        roles,
        fake_llm,
        propose(source, relationships=[decide(orders, "accept")]),
        as_role="viewer",
    )

    assert status == "refused"
    assert relationship(roles, source, ORDERS)["status"] == "suggested"
