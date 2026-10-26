"""The assistant's ``render_diagram`` tool (#399): Mermaid built from the DW model or the
lineage graph, never from the model's guess. The model is the scripted fake provider."""

from __future__ import annotations

import pytest

from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.kpis import test_kpi_suggestions
from tests.lineage import test_lineage

model = test_chat.model  # the assistant's model (a fixture)
dw_model = test_kpi_suggestions.model  # dim_customer, fact_loan, mart_loan
graph = test_lineage.graph  # the hand-built lineage graph (a fixture)


def test_a_star_schema_request_returns_a_diagram_built_from_the_model(
    roles, model, fake_llm, dw_model
):
    status, seen = use(roles, fake_llm, "render_diagram", kind="star_schema")

    assert status == "ok"
    result = payload(seen)
    source = result["mermaid"]
    assert source.startswith("erDiagram")
    assert 'fact_loan }o--|| dim_customer : "customer_key"' in source
    assert "measure amount" in source and "fk customer_key FK" in source
    assert "mart_loan" in source
    assert "```mermaid" in result["note"]


def test_a_dimension_scope_shows_the_facts_that_use_it(roles, model, fake_llm, dw_model):
    _, seen = use(roles, fake_llm, "render_diagram", kind="star_schema", scope="dim_customer")

    source = payload(seen)["mermaid"]
    assert "fact_loan }o--|| dim_customer" in source and "mart_loan" not in source


def test_an_unknown_scope_is_an_error_not_an_invented_diagram(roles, model, fake_llm, dw_model):
    status, seen = use(roles, fake_llm, "render_diagram", kind="star_schema", scope="nope")

    assert status == "error" and "not found" in seen


def test_without_a_data_warehouse_there_is_nothing_to_draw(roles, model, fake_llm):
    status, seen = use(roles, fake_llm, "render_diagram", kind="star_schema")

    assert status == "error" and "no_data_warehouse" in seen


def test_a_lineage_diagram_has_the_graphs_nodes_and_edges(roles, model, fake_llm, graph):
    status, seen = use(
        roles, fake_llm, "render_diagram", kind="lineage", scope=graph["kpi"], direction="upstream"
    )

    assert status == "ok"
    source = payload(seen)["mermaid"]
    assert source.startswith("flowchart LR")
    assert "Revenue (kpi)" in source and "mart_orders.amount_m" in source
    assert "-->|kpi|" in source and "-->|value|" in source


def test_a_lineage_diagram_needs_a_node_id(roles, model, fake_llm):
    status, seen = use(roles, fake_llm, "render_diagram", kind="lineage")

    assert status == "error" and "scope_required" in seen


@pytest.mark.parametrize("name", ['bad"name<script>', "a b;c"])
def test_object_names_cannot_break_out_of_the_diagram(name):
    from dawam.modules.assistant.internal import diagrams

    assert diagrams.ident(name).isidentifier()
    assert not set('"<>;') & set(diagrams.label(name))
