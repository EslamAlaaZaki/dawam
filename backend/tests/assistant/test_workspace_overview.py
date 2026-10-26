"""The ``workspace_overview`` tool (spec §6.16): everything the member can see, with ids."""

from __future__ import annotations

from tests.assistant import test_chat, test_source_tools
from tests.assistant.test_source_tools import level, payload, system_id, use
from tests.authz.matrix import dw_table_id
from tests.sources.test_extraction import add_system

model = test_chat.model
settings = test_chat.settings
source = test_source_tools.source


def test_lists_source_systems_layers_and_counts_with_ids(roles, model, fake_llm, source):
    status, seen = use(roles, fake_llm, "workspace_overview")

    assert status == "ok"
    overview = payload(seen)
    [system] = overview["source_systems"]
    assert system["id"] == system_id(source)
    assert system["engine"] and system["connection"] in ("tested", "untested")
    assert system["snapshot"]["count"] == 1 and system["snapshot"]["table_count"] > 0
    assert system["id"] in system["link"]
    assert [layer["layer"] for layer in overview["dw_layers"]] == ["staging", "core", "mart"]
    assert all(layer["table_count"] == 0 for layer in overview["dw_layers"])
    assert overview["kpi_count"] == 0


def test_a_system_without_a_connection_or_snapshot_says_so(roles, model, fake_llm):
    system = add_system(roles)

    _, seen = use(roles, fake_llm, "workspace_overview")

    [listed] = payload(seen)["source_systems"]
    assert listed["id"] == system_id(system)
    assert listed["connection"] == "not_connected"
    assert "engine" not in listed and "snapshot" not in listed


def test_a_viewer_gets_it_but_no_connection_details(roles, model, fake_llm, source):
    status, seen = use(roles, fake_llm, "workspace_overview", as_role="viewer")

    assert status == "ok"
    assert "host" not in seen and "username" not in seen


def test_it_needs_only_the_metadata_level(roles, model, fake_llm, source):
    level(roles, "metadata")

    status, _ = use(roles, fake_llm, "workspace_overview", as_role="viewer")

    assert status == "ok"


def test_dw_layers_list_their_tables_with_ids(roles, model, fake_llm):
    table_id = dw_table_id(roles)

    _, seen = use(roles, fake_llm, "workspace_overview")

    layers = {layer["layer"]: layer for layer in payload(seen)["dw_layers"]}
    assert layers["core"]["table_count"] == 1
    assert layers["core"]["tables"][0]["id"] == table_id
    assert layers["staging"]["table_count"] == layers["mart"]["table_count"] == 0
