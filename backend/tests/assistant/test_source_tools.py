"""The assistant's source tools (spec §6.16, stories 72, 142, 143).

The model is the scripted fake provider, so the tests see exactly what each tool put in
front of it: the tool's result as quoted data, per role and per data-sharing level. The
Source System is the seeded sample source; ``national_id`` is one of its Protected Columns.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dawam.modules.assistant.internal.tools import MAX_RESULT_CHARS, _json
from dawam.modules.llm import FakeAdapter, Reply
from tests.assistant import test_chat
from tests.assistant.test_chat import ask, conversation, tool, tool_names
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source
model = test_chat.model
settings = test_chat.settings
"""The chat tests' fixtures: an assigned agent model, and a small tool-call cap."""

NATIONAL_ID = "29001011234567"
FULL_NAME = "Amira Hassan"


def level(roles: RoleClients, value: str) -> None:
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={"internal_only": False, "data_sharing_level": value},
    )
    assert response.status_code == 200, response.text


def system_id(system: str) -> str:
    return system.rsplit("/", 1)[1]


def table_of(roles: RoleClients, system: str, name: str = "customers") -> dict:
    [table] = [t for t in schema(roles, system)["tables"] if t["name"] == name]
    return table


@pytest.fixture
def source(roles: RoleClients, sample_source: SampleSource) -> str:
    """An extracted Source System with ``national_id`` confirmed as direct-identifier PII."""
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    queue = roles.client("editor").get(f"{system}/pii-findings").json()["items"]
    [national] = [f for f in queue if f["column"] == "national_id"]
    confirmed = roles.client("editor").post(f"{system}/pii-findings/{national['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return system


@pytest.fixture
def profiled(roles: RoleClients, source: str) -> str:
    """``customers`` profiled with its top values switched on."""
    table_id = table_of(roles, source)["id"]
    put = roles.client("owner").put(
        f"{source}/tables/{table_id}/profiling-settings", json={"enabled": True}
    )
    assert put.status_code == 200, put.text
    started = roles.client("editor").post(f"{source}/profiling", json={"table_ids": [table_id]})
    assert started.status_code == 202, started.text
    return source


def use(
    roles: RoleClients, fake_llm: FakeAdapter, name: str, as_role: str = "viewer", **arguments: Any
) -> tuple[str, str]:
    """Have the model call one tool; return the call's status and what the model then saw."""
    fake_llm.calls.clear()
    fake_llm.script(Reply(tool_calls=(tool(name, **arguments),)), Reply(text="ok"))
    client = roles.client(as_role)
    events = ask(client, roles, conversation(client, roles), "Look")
    [(_, shown)] = [e for e in events if e[0] == "tool"]
    return shown["status"], fake_llm.calls[1].messages[-1].content or ""


def payload(seen: str) -> Any:
    lines = seen.splitlines()
    assert lines[0].startswith("<data source=") and lines[-1] == "</data>", seen
    return json.loads("\n".join(lines[1:-1]))


def everything_the_model_saw(fake_llm: FakeAdapter) -> str:
    return " ".join(m.content or "" for call in fake_llm.calls for m in call.messages)


# -- search_catalog and get_object ---------------------------------------------------------


def test_search_catalog_finds_objects_and_links_to_them(roles, model, fake_llm, source):
    status, seen = use(roles, fake_llm, "search_catalog", system_id=system_id(source), query="cust")

    assert status == "ok"
    hits = payload(seen)
    customers = table_of(roles, source)
    [table_hit] = [h for h in hits if h["kind"] == "table" and h["name"] == "customers"]
    assert table_hit["id"] == customers["id"]
    assert customers["id"] in table_hit["link"] and system_id(source) in table_hit["link"]
    column_hits = [h for h in hits if h["kind"] == "column"]
    assert column_hits and all(h["link"] and h["id"] in h["link"] for h in column_hits)


def test_get_object_describes_a_table_and_protected_columns_by_name_and_category_only(
    roles, model, fake_llm, source
):
    customers = table_of(roles, source)

    status, seen = use(
        roles, fake_llm, "get_object", kind="table", system_id=system_id(source), id=customers["id"]
    )

    assert status == "ok"
    result = payload(seen)
    assert result["name"] == "customers" and customers["id"] in result["link"]
    columns = {c["name"]: c for c in result["columns"]}
    assert columns["cust_no"]["data_type"] == "integer"
    assert columns["branch_code"]["id"] in columns["branch_code"]["link"]
    protected = columns["national_id"]
    assert set(protected) == {"id", "name", "is_protected", "pii_category", "link"}
    assert protected["is_protected"] is True
    assert protected["pii_category"] == "direct_identifier"
    assert "National ID number" not in seen  # not even its comment


def test_get_object_describes_a_column(roles, model, fake_llm, source):
    customers = table_of(roles, source)
    columns = {c["name"]: c for c in customers["columns"]}

    ok, seen = use(
        roles,
        fake_llm,
        "get_object",
        kind="column",
        system_id=system_id(source),
        id=columns["cust_no"]["id"],
    )
    protected_status, protected = use(
        roles,
        fake_llm,
        "get_object",
        kind="column",
        system_id=system_id(source),
        id=columns["national_id"]["id"],
    )

    assert ok == "ok" and payload(seen)["table"] == "customers"
    assert protected_status == "ok" and "National ID number" not in protected
    assert payload(protected)["is_protected"] is True


def test_get_object_needs_a_source_system_for_source_objects(roles, model, fake_llm, source):
    customers = table_of(roles, source)

    status, seen = use(roles, fake_llm, "get_object", kind="table", id=customers["id"])
    missing, _ = use(
        roles,
        fake_llm,
        "get_object",
        kind="table",
        system_id=system_id(source),
        id="00000000-0000-0000-0000-000000000000",
    )

    assert status == "error" and "system_id" in seen
    assert missing == "error"


# -- get_profile: the data-sharing level and Protected Columns -----------------------------


def test_get_profile_is_refused_below_the_profiles_level(roles, model, fake_llm, profiled):
    customers = table_of(roles, profiled)

    status, seen = use(
        roles, fake_llm, "get_profile", system_id=system_id(profiled), table_id=customers["id"]
    )

    assert status == "refused" and "profiles" in seen
    assert "get_profile" not in tool_names(fake_llm)


def test_get_profile_gives_statistics_but_no_values_at_the_profiles_level(
    roles, model, fake_llm, profiled
):
    level(roles, "profiles")
    customers = table_of(roles, profiled)

    status, seen = use(
        roles, fake_llm, "get_profile", system_id=system_id(profiled), table_id=customers["id"]
    )

    assert status == "ok"
    columns = {c["name"]: c for c in payload(seen)["columns"]}
    assert columns["cust_no"]["distinct_count"] == 3 and columns["cust_no"]["row_count"] == 3
    assert columns["branch_code"]["null_pct"] == 0
    for column in columns.values():
        assert not {"min", "max", "top_values"} & set(column)
    assert "CAI" not in seen and "ALX" not in seen


def test_get_profile_gives_top_values_min_and_max_only_at_the_samples_level(
    roles, model, fake_llm, profiled
):
    level(roles, "samples")
    customers = table_of(roles, profiled)

    status, seen = use(
        roles, fake_llm, "get_profile", system_id=system_id(profiled), table_id=customers["id"]
    )

    assert status == "ok"
    columns = {c["name"]: c for c in payload(seen)["columns"]}
    assert (columns["cust_no"]["min"], columns["cust_no"]["max"]) == ("1", "3")
    assert columns["branch_code"]["top_values"] == [
        {"value": "CAI", "count": 2},
        {"value": "ALX", "count": 1},
    ]


@pytest.mark.parametrize("value", ["profiles", "samples"])
def test_get_profile_never_sends_a_protected_column_beyond_its_name_and_category(
    roles, model, fake_llm, profiled, value
):
    level(roles, value)
    customers = table_of(roles, profiled)

    status, seen = use(
        roles, fake_llm, "get_profile", system_id=system_id(profiled), table_id=customers["id"]
    )

    assert status == "ok"
    columns = {c["name"]: c for c in payload(seen)["columns"]}
    for name in ("national_id", "full_name", "email", "phone"):
        assert set(columns[name]) == {"id", "name", "is_protected", "pii_category", "link"}
    assert columns["national_id"]["pii_category"] == "direct_identifier"
    everything = everything_the_model_saw(fake_llm)
    assert NATIONAL_ID not in everything and FULL_NAME not in everything
    assert "amira.hassan" not in everything


def test_get_profile_of_one_column(roles, model, fake_llm, profiled):
    level(roles, "samples")
    customers = table_of(roles, profiled)
    columns = {c["name"]: c for c in customers["columns"]}

    _, seen = use(
        roles,
        fake_llm,
        "get_profile",
        system_id=system_id(profiled),
        table_id=customers["id"],
        column_id=columns["branch_code"]["id"],
    )
    _, protected = use(
        roles,
        fake_llm,
        "get_profile",
        system_id=system_id(profiled),
        table_id=customers["id"],
        column_id=columns["national_id"]["id"],
    )

    assert [c["name"] for c in payload(seen)["columns"]] == ["branch_code"]
    assert NATIONAL_ID not in protected
    assert payload(protected)["columns"][0]["is_protected"] is True


# -- get_snapshot_diff ---------------------------------------------------------------------


def test_get_snapshot_diff_defaults_to_the_last_two_snapshots(
    roles, model, fake_llm, scratch_source
):
    scratch_source["run"]("CREATE TABLE orders (id integer PRIMARY KEY, note text);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    only_one, seen = use(roles, fake_llm, "get_snapshot_diff", system_id=system_id(system))
    scratch_source["run"](
        "ALTER TABLE orders ADD COLUMN placed_on date; CREATE TABLE fresh (id int);"
    )
    extract(roles, system)

    status, seen_diff = use(roles, fake_llm, "get_snapshot_diff", system_id=system_id(system))

    assert only_one == "error" and "two Snapshots" in seen
    assert status == "ok"
    tables = {t["name"]: t for t in payload(seen_diff)["tables"]}
    assert tables["fresh"]["change"] == "added" and tables["fresh"]["id"] in tables["fresh"]["link"]
    assert [c["name"] for c in tables["orders"]["columns"]] == ["placed_on"]


def test_get_snapshot_diff_sends_a_protected_column_by_name_and_change_only(
    roles, model, fake_llm, scratch_source
):
    scratch_source["run"](
        "CREATE TABLE people (id integer PRIMARY KEY, email text DEFAULT 'old@secret.example',"
        " phone text DEFAULT '+20 100 secret', city text DEFAULT 'Cairo');"
        "COMMENT ON COLUMN people.email IS 'e.g. old@secret.example';"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    scratch_source["run"](
        "ALTER TABLE people ALTER COLUMN email SET DEFAULT 'new@secret.example';"
        "COMMENT ON COLUMN people.email IS 'e.g. new@secret.example';"
        "ALTER TABLE people ALTER COLUMN city SET DEFAULT 'Giza';"
        "ALTER TABLE people DROP COLUMN phone;"
    )
    extract(roles, system)

    status, seen = use(roles, fake_llm, "get_snapshot_diff", system_id=system_id(system))

    assert status == "ok"
    assert "secret" not in everything_the_model_saw(fake_llm)
    [people] = payload(seen)["tables"]
    columns = {c["name"]: c for c in people["columns"]}
    assert set(columns["email"]) == {"id", "name", "change", "link"}  # changed
    assert set(columns["phone"]) == {"id", "name", "change", "link"}  # removed by the newer one
    assert columns["phone"]["change"] == "removed"
    assert columns["city"]["fields"] == [
        {"field": "default", "before": "'Cairo'::text", "after": "'Giza'::text"}
    ]


def test_a_view_definition_is_withheld_below_the_samples_level(
    roles, model, fake_llm, scratch_source
):
    scratch_source["run"](
        "CREATE TABLE people (id integer PRIMARY KEY, email text);"
        "CREATE VIEW leaky AS SELECT id FROM people WHERE email = 'x@secret.example';"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    [view] = [t for t in schema(roles, system)["tables"] if t["name"] == "leaky"]
    ask_for = {"system_id": system_id(system), "id": view["id"], "kind": "table"}
    level(roles, "documents")

    _, withheld = use(roles, fake_llm, "get_object", **ask_for)
    level(roles, "samples")
    _, shown = use(roles, fake_llm, "get_object", **ask_for)

    assert "secret.example" not in withheld and "Withheld" in payload(withheld)["definition_note"]
    assert "view_definition" not in payload(withheld)
    assert "secret.example" in payload(shown)["view_definition"]


def test_a_routine_definition_is_withheld_below_the_samples_level(roles, model, fake_llm, source):
    [routine] = [r for r in schema(roles, source)["routines"] if r["name"] == "account_turnover"]
    ask_for = {"system_id": system_id(source), "id": routine["id"], "kind": "routine"}

    _, withheld = use(roles, fake_llm, "get_object", **ask_for)
    level(roles, "samples")
    _, shown = use(roles, fake_llm, "get_object", **ask_for)

    assert (
        "definition" not in payload(withheld) and "Withheld" in payload(withheld)["definition_note"]
    )
    assert "transactions" in payload(shown)["definition"]


def test_a_long_result_is_cut_by_whole_items_and_stays_valid_json():
    rows = [{"name": f"column_{i}", "note": "x" * 200} for i in range(500)]

    text = _json({"columns": rows, "name": "big"})

    assert len(text) <= MAX_RESULT_CHARS
    cut = json.loads(text)
    assert cut["truncated"] is True and 0 < len(cut["columns"]) < 500
    assert all(set(r) == {"name", "note"} for r in cut["columns"])
    assert json.loads(_json(rows))["items"][0]["name"] == "column_0"


# -- get_pii_findings ----------------------------------------------------------------------


def test_get_pii_findings_lists_columns_never_values_for_editors_and_owners(
    roles, model, fake_llm, source
):
    status, seen = use(roles, fake_llm, "get_pii_findings", "editor", system_id=system_id(source))

    assert status == "ok"
    found = {(f["table"], f["column"]): f for f in payload(seen)}
    assert found[("customers", "national_id")]["category"] == "direct_identifier"
    assert found[("customers", "national_id")]["status"] == "confirmed"
    assert NATIONAL_ID not in everything_the_model_saw(fake_llm)


def test_get_pii_findings_is_not_offered_to_a_viewer_and_is_refused_if_called(
    roles, model, fake_llm, source
):
    status, seen = use(roles, fake_llm, "get_pii_findings", "viewer", system_id=system_id(source))

    assert status == "refused" and "Refused" in seen
    assert "get_pii_findings" not in tool_names(fake_llm)


# -- search_documents, list_files, read_file -----------------------------------------------


def upload(roles: RoleClients, system: str, name: str, text: str) -> str:
    response = roles.client("owner").post(
        f"{system}/files", files={"file": (name, text.encode(), "text/markdown")}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_search_documents_cites_the_document_and_section_at_the_documents_level(
    roles, model, fake_llm, source
):
    file_id = upload(
        roles, source, "design.md", "# Overview\nIntro\n\n# Settlement\nSettlement runs nightly."
    )
    level(roles, "documents")

    status, seen = use(roles, fake_llm, "search_documents", query="settlement runs")

    assert status == "ok"
    [passage, *_] = payload(seen)
    assert passage["document"] == "design.md" and passage["section"]
    assert "Settlement runs nightly" in passage["text"]
    assert file_id in passage["link"] and passage["file_id"] == file_id


def test_search_documents_is_refused_below_the_documents_level(roles, model, fake_llm, source):
    upload(roles, source, "design.md", "# Settlement\nSettlement runs nightly.")

    status, seen = use(roles, fake_llm, "search_documents", query="settlement")

    assert status == "refused" and "documents" in seen
    assert "search_documents" not in tool_names(fake_llm)
    assert "nightly" not in seen


def test_list_files_names_a_source_systems_files(roles, model, fake_llm, source):
    file_id = upload(roles, source, "notes.md", "# Notes")

    status, seen = use(roles, fake_llm, "list_files", system_id=system_id(source))

    assert status == "ok" and file_id in seen and "notes.md" in seen


def test_read_file_returns_the_document_text_at_the_documents_level(roles, model, fake_llm, source):
    file_id = upload(roles, source, "notes.md", "# Notes\nthe ledger closes at six")
    level(roles, "documents")

    status, seen = use(roles, fake_llm, "read_file", file_id=file_id)

    assert status == "ok" and "the ledger closes at six" in seen


# -- every tool, every role, every level ---------------------------------------------------

METADATA_TOOLS = {
    "search_catalog",
    "get_object",
    "get_snapshot_diff",
    "list_files",
    "run_validation",
}
LEVELS = [
    ("metadata", METADATA_TOOLS),
    ("profiles", METADATA_TOOLS | {"get_profile"}),
    ("documents", METADATA_TOOLS | {"get_profile", "search_documents", "read_file"}),
    ("samples", METADATA_TOOLS | {"get_profile", "search_documents", "read_file"}),
]


@pytest.mark.parametrize(("value", "expected"), LEVELS)
@pytest.mark.parametrize("role", ["viewer", "editor", "owner"])
def test_each_role_is_offered_the_tools_its_policy_and_the_level_allow(
    roles, model, fake_llm, role, value, expected
):
    level(roles, value)
    client = roles.client(role)
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="hi"))

    ask(client, roles, conversation(client, roles), "hello")

    offered = tool_names(fake_llm) - {"generate_file"}
    extra = {"get_pii_findings", "propose_changes"} if role != "viewer" else set()
    assert offered == expected | extra


# -- run_validation ------------------------------------------------------------------------


def test_run_validation_returns_problems_and_coverage(roles, model, fake_llm):
    dw = f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"
    editor = roles.client("editor")
    assert editor.post(dw, json={"target_platform": "postgresql"}).status_code == 201
    table = editor.post(
        f"{dw}/tables", json={"layer": "core", "name": "dim_customer", "kind": "dimension"}
    ).json()
    editor.post(
        f"{dw}/tables/{table['id']}/columns",
        json={"name": "name", "data_type": {"type": "string"}, "role": "attribute"},
    )

    status, seen = use(roles, fake_llm, "run_validation")

    result = payload(seen)
    assert status == "ok"
    assert result["coverage"]["coverage"] == {"total": 1, "covered": 0, "percent": 0}
    assert [p["code"] for p in result["problems"]] == ["unmapped_column"]


def test_run_validation_reports_a_missing_data_warehouse(roles, model, fake_llm):
    status, seen = use(roles, fake_llm, "run_validation")

    assert status == "error" and "not_set_up" in seen
