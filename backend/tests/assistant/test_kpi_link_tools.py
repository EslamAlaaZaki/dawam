"""The assistant's KPI link tools: the AI proposes links and formula SQL as a Change Set
(spec §6.13, story 76a). The model is the scripted fake provider."""

from __future__ import annotations

from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.kpis import test_kpi_links
from tests.kpis.test_kpis import created
from tests.roles import RoleClients

model = test_chat.model  # the assistant's model (a fixture)
warehouse = test_kpi_links.model  # a Data Warehouse with Core and Mart tables (a fixture)


def test_the_candidates_tool_lists_unlinked_kpis_with_the_dw_schema(
    roles: RoleClients, model, warehouse, fake_llm
):
    kpi = created(roles, name="Total loans", formula_text="Sum of loan amounts")

    status, seen = use(roles, fake_llm, "get_kpi_link_candidates", "editor")

    assert status == "ok"
    body = payload(seen)
    assert [k["id"] for k in body["kpis"]] == [kpi["id"]]
    tables = {t["name"]: t for t in body["dw_schema"]["tables"]}
    assert {c["name"] for c in tables["fact_loan"]["columns"]} >= {"amount", "rate"}
    assert tables["fact_loan"]["columns"][0]["id"]


def test_the_propose_tool_makes_a_change_set_and_changes_nothing(
    roles: RoleClients, model, warehouse, fake_llm
):
    kpi = created(roles, name="Total loans")

    status, _ = use(
        roles,
        fake_llm,
        "propose_kpi_links",
        "editor",
        title="Link the loan KPIs",
        items=[
            {
                "kpi_id": kpi["id"],
                "formula_sql": "SELECT SUM(amount) FROM fact_loan",
                "dw_column_ids": [warehouse["amount"]["id"]],
                "label": "Total loans",
            }
        ],
    )

    assert status == "ok"
    base = f"/api/v1/workspaces/{roles.workspace_id}"
    [proposed] = roles.client("editor").get(f"{base}/change-sets").json()["items"]
    assert proposed["origin"] == "ai" and proposed["status"] == "pending"
    unchanged = roles.client("editor").get(f"{base}/kpis/{kpi['id']}").json()
    assert unchanged["formula_sql"] is None
    assert unchanged["version"] == 1


def test_a_proposal_the_dw_schema_rejects_is_reported_to_the_model(
    roles: RoleClients, model, warehouse, fake_llm
):
    kpi = created(roles, name="Total loans")

    status, _ = use(
        roles,
        fake_llm,
        "propose_kpi_links",
        "editor",
        title="Bad",
        items=[{"kpi_id": kpi["id"], "formula_sql": "SELECT SUM(nope) FROM fact_loan"}],
    )

    assert status == "error"
