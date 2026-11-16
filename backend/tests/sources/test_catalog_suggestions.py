"""Data catalog suggestions (spec §6.19, #626).

Driven through the HTTP API with jobs run inline; the model is the scripted fake provider
behind a registered agent model, so ``fake_llm.calls`` shows exactly what the AI saw. Most
tests use a Schema Import (no Connection); the sample-rows test extracts a scratch source.
The audit trail is read through its service.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dawam.modules.audit import AuditService
from dawam.modules.jobs import JobService, QueuedJobRunner
from dawam.modules.llm import Reply, ToolCall
from dawam.modules.sources import CATALOG_SUGGEST_JOB
from tests.llm.test_roles_budgets import register, use_models
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_pii_ai_detect import share_documents, upload_document
from tests.sources.test_profiling import profile_tables
from tests.sources.test_schema_browser import schema
from tests.sources.test_schema_import import csvs, upload

scratch_source = test_extraction.scratch_source

TABLES = (
    "schema,table,kind,row_count\n"
    "sales,customer,table,5\n"
    "sales,orders,table,50\n"
    "sales,country,table,3\n"
)
COLUMNS = (
    "schema,table,column,data_type\n"
    "sales,customer,id,integer\n"
    "sales,customer,name,varchar(40)\n"
    "sales,orders,id,integer\n"
    "sales,orders,status_code,varchar(2)\n"
    "sales,country,code,char(2)\n"
)


@pytest.fixture
def model(admin_client, fake_llm, roles: RoleClients):
    assert roles.workspace_id
    agent = register(admin_client, "catalog-model")
    assert use_models(roles.client("owner"), roles.workspace_id, agent).status_code == 200
    fake_llm.calls.clear()
    return agent


def imported(roles: RoleClients, code: str = "sales") -> str:
    """A Source System from a Schema Import of ``TABLES``: no Connection."""
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": code, "code": code}
    )
    assert response.status_code == 201, response.text
    system = f"/api/v1/workspaces/{roles.workspace_id}/systems/{response.json()['id']}"
    upload(roles, system, csvs(tables=TABLES, columns=COLUMNS))
    return system


def ids(roles: RoleClients, system: str) -> dict[str, Any]:
    """``table`` -> its id, and ``table.column`` -> the column's id."""
    out: dict[str, Any] = {}
    for table in schema(roles, system)["tables"]:
        out[table["name"]] = table["id"]
        for column in table["columns"]:
            out[f"{table['name']}.{column['name']}"] = column["id"]
    return out


def answer(*tables: dict) -> Reply:
    return Reply(text=json.dumps({"tables": list(tables)}))


def path(system: str) -> str:
    return f"{system}/catalog-suggestions"


def run(roles: RoleClients, system: str, as_role: str = "editor") -> dict:
    client = roles.client(as_role)
    started = client.post(path(system))
    assert started.status_code == 202, started.text
    latest = client.get(path(system))
    assert latest.status_code == 200, latest.text
    job = latest.json()["job"]
    assert job["job_id"] == started.json()["job_id"]
    return job


def listed(roles: RoleClients, system: str, status: str = "pending", **params) -> list[dict]:
    response = roles.client("editor").get(
        f"{path(system)}/items", params={"status": status, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def key(item: dict) -> tuple[str, str | None, str]:
    return (item["table"], item["column"], item["field"])


def by_key(items: list[dict]) -> dict[tuple[str, str | None, str], dict]:
    return {key(i): i for i in items}


def chats(fake_llm) -> list:
    return [c for c in fake_llm.calls if c.kind == "chat"]


def shown(call) -> dict:
    """The tables the AI was shown (the JSON inside the user turn's <data> tags)."""
    text = next(m.content for m in call.messages if m.role == "user")
    data = json.loads(text.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    return {t["table"]: t for t in data["tables"]}


def table_of(roles: RoleClients, system: str, name: str) -> dict:
    return next(t for t in schema(roles, system)["tables"] if t["name"] == name)


FULL = (
    {
        "table": "sales.customer",
        "description": "One row per customer.",
        "classification": "master",
        "scd_hint": "Names change. Keep history (type 2).",
        "columns": [
            {"column": "id", "description": "Surrogate key."},
            {"column": "name", "description": "The customer's name."},
        ],
    },
    {
        "table": "sales.orders",
        "description": "One row per order.",
        "classification": "transactional",
        "scd_hint": "Not a dimension.",
        "columns": [{"column": "status_code", "description": "Order state code."}],
    },
)


# -- the job: fills gaps only --------------------------------------------------------------


def test_the_job_fills_only_empty_fields_and_a_rerun_replaces_the_pending_ones(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    found = ids(roles, system)
    editor = roles.client("editor")
    customer = table_of(roles, system, "customer")
    filled = editor.patch(
        f"{system}/tables/{found['customer']}",
        json={"version": customer["version"], "description": "Written by a person."},
    )
    assert filled.status_code == 200, filled.text
    fake_llm.script(answer(*FULL))

    job = run(roles, system)

    assert job["status"] == "succeeded"
    seen = shown(chats(fake_llm)[0])
    assert seen["sales.customer"]["fill"]["table"] == ["classification", "scd_hint"]
    assert seen["sales.customer"]["description"] == "Written by a person."
    assert seen["sales.orders"]["fill"] == {
        "table": ["description", "classification", "scd_hint"],
        "columns": ["id", "status_code"],
    }
    pending = by_key(listed(roles, system))
    assert ("customer", None, "description") not in pending  # a person's value stays
    assert pending["customer", None, "classification"]["value"] == "master"
    assert pending["customer", "name", "description"]["value"] == "The customer's name."
    assert job["made"] == len(pending) and job["skipped"] >= 1
    assert table_of(roles, system, "customer")["description"] == "Written by a person."

    fake_llm.script(answer({"table": "sales.country", "description": "Countries."}))
    again = run(roles, system)

    after = by_key(listed(roles, system))
    assert list(after) == [("country", None, "description")]
    assert again["made"] == 1


def test_a_rejected_value_goes_to_the_model_and_is_never_suggested_again(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer({"table": "sales.country", "description": "A list of countries."}))
    run(roles, system)
    [rejected] = listed(roles, system)
    response = roles.client("editor").post(f"{path(system)}/reject", json={"ids": [rejected["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["decided"][0]["status"] == "rejected"
    fake_llm.calls.clear()
    fake_llm.script(answer({"table": "sales.country", "description": "a list of  COUNTRIES. "}))

    job = run(roles, system)

    seen = shown(chats(fake_llm)[0])["sales.country"]
    assert seen["rejected"] == [{"field": "description", "value": "A list of countries."}]
    assert "description" in seen["fill"]["table"]
    assert listed(roles, system) == []
    assert job["made"] == 0 and job["skipped"] >= 1
    assert [s["value"] for s in listed(roles, system, "rejected")] == ["A list of countries."]


def test_scd_hints_are_suggested_only_for_master_and_reference_tables(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    found = ids(roles, system)
    country = table_of(roles, system, "country")
    response = roles.client("editor").patch(
        f"{system}/tables/{found['country']}",
        json={"version": country["version"], "classification": "log"},
    )
    assert response.status_code == 200, response.text
    fake_llm.script(
        answer(
            *FULL,
            {"table": "sales.country", "scd_hint": "Never changes.", "description": "Codes."},
        )
    )

    run(roles, system)

    seen = shown(chats(fake_llm)[0])
    assert "scd_hint" not in seen["sales.country"]["fill"]["table"]  # classified log
    pending = by_key(listed(roles, system))
    assert pending["customer", None, "scd_hint"]["value"].startswith("Names change")
    assert ("orders", None, "scd_hint") not in pending  # suggested transactional
    assert ("country", None, "scd_hint") not in pending
    assert ("country", None, "description") in pending


def test_pending_suggestions_expire_when_their_object_leaves_the_snapshot(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    before = by_key(listed(roles, system))
    gone = before["orders", "status_code", "description"]

    upload(  # a new Snapshot without orders.status_code and without customer
        roles,
        system,
        csvs(
            tables="schema,table,kind,row_count\nsales,orders,table,50\nsales,country,table,3\n",
            columns=(
                "schema,table,column,data_type\nsales,orders,id,integer\n"
                "sales,country,code,char(2)\n"
            ),
        ),
    )

    expired = by_key(listed(roles, system, "expired"))
    assert ("orders", "status_code", "description") in expired
    assert ("customer", None, "classification") in expired
    assert ("orders", None, "description") in by_key(listed(roles, system))
    response = roles.client("editor").post(f"{path(system)}/accept", json={"ids": [gone["id"]]})
    assert response.status_code == 200, response.text
    assert response.json() == {"decided": [], "skipped": [gone["id"]]}


# -- accepting and rejecting ---------------------------------------------------------------


def test_accepting_copies_the_value_with_an_ai_origin_and_audits_it_as_the_ais(
    roles: RoleClients, model, fake_llm, app
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    pending = by_key(listed(roles, system))
    description = pending["customer", None, "description"]
    column = pending["customer", "name", "description"]
    editor = roles.client("editor")

    response = editor.post(
        f"{path(system)}/accept", json={"ids": [description["id"], column["id"]]}
    )

    assert response.status_code == 200, response.text
    assert {s["status"] for s in response.json()["decided"]} == {"accepted"}
    customer = table_of(roles, system, "customer")
    assert customer["description"] == "One row per customer."
    assert customer["ai_fields"] == ["description"]
    name = next(c for c in customer["columns"] if c["name"] == "name")
    assert (name["description"], name["ai_fields"]) == ("The customer's name.", ["description"])
    audit = AuditService(app.state.engine)
    [entry] = audit.list(roles.workspace_id, entity_type="source_table", entity_id=customer["id"])
    assert (entry.old, entry.new, entry.via) == (
        {"description": None},
        {"description": "One row per customer."},
        "ai",
    )

    # A person's edit takes the AI mark away.
    edited = editor.patch(
        f"{system}/tables/{customer['id']}",
        json={"version": customer["version"], "description": "Our customers."},
    )
    assert edited.status_code == 200, edited.text
    assert table_of(roles, system, "customer")["ai_fields"] == []


def test_edit_then_accept_saves_a_persons_value_with_no_ai_mark(
    roles: RoleClients, model, fake_llm, app
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    suggestion = by_key(listed(roles, system))["customer", None, "classification"]

    response = roles.client("owner").post(
        f"{path(system)}/accept-edited", json={"id": suggestion["id"], "value": "reference"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["value"], body["edited_value"]) == (
        "accepted",
        "master",
        "reference",
    )
    customer = table_of(roles, system, "customer")
    assert customer["classification"] == "reference" and customer["ai_fields"] == []
    audit = AuditService(app.state.engine)
    [entry] = audit.list(roles.workspace_id, entity_type="source_table", entity_id=customer["id"])
    assert (entry.new, entry.via) == ({"classification": "reference"}, "user")
    again = roles.client("owner").post(
        f"{path(system)}/accept-edited", json={"id": suggestion["id"], "value": "log"}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "suggestion_not_pending"
    bad = by_key(listed(roles, system))["orders", None, "classification"]
    invalid = roles.client("owner").post(
        f"{path(system)}/accept-edited", json={"id": bad["id"], "value": "fact"}
    )
    assert invalid.status_code == 422


def test_rejecting_leaves_the_value_empty_and_is_audited(roles: RoleClients, model, fake_llm, app):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    suggestion = by_key(listed(roles, system))["orders", None, "description"]

    response = roles.client("editor").post(
        f"{path(system)}/reject", json={"ids": [suggestion["id"]]}
    )

    assert response.status_code == 200, response.text
    [decided] = response.json()["decided"]
    assert decided["status"] == "rejected" and decided["decided_by"]
    assert table_of(roles, system, "orders")["description"] is None
    audit = AuditService(app.state.engine)
    [entry] = audit.list(
        roles.workspace_id, entity_type="catalog_suggestion", entity_id=suggestion["id"]
    )
    assert (entry.old, entry.new) == ({"status": "pending"}, {"status": "rejected"})


def test_accept_all_takes_every_pending_suggestion_of_a_table(roles: RoleClients, model, fake_llm):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    found = ids(roles, system)

    response = roles.client("editor").post(
        f"{path(system)}/accept-all", json={"table_id": found["customer"]}
    )

    assert response.status_code == 200, response.text
    assert {s["table"] for s in response.json()["decided"]} == {"customer"}
    assert {i["table"] for i in listed(roles, system)} == {"orders"}
    assert table_of(roles, system, "customer")["classification"] == "master"
    counts = roles.client("editor").get(f"{path(system)}/items").json()["counts"]
    assert counts["accepted"] == 5 and counts["pending"] > 0


# -- one job per system, permissions -------------------------------------------------------


def test_one_job_runs_per_system_at_a_time(roles: RoleClients, model, fake_llm):
    system = imported(roles)
    system_id = system.rsplit("/", 1)[1]
    queue = QueuedJobRunner()
    queue.register(CATALOG_SUGGEST_JOB, lambda params, ctx: None)
    state = roles.app.state
    waiting = JobService(state.engine, runner=queue, clock=state.services.clock).submit(
        roles.workspace_id,
        CATALOG_SUGGEST_JOB,
        {"source_system_id": system_id},
        title="Suggest catalog entries",
        created_by=roles.user("editor").id,
    )
    editor = roles.client("editor")

    refused = editor.post(path(system))

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "catalog_suggestions_running"
    assert editor.get(path(system)).json()["job"]["job_id"] == str(waiting.id)


def test_viewers_get_403_and_see_accepted_values_only(roles: RoleClients, model, fake_llm):
    system = imported(roles)
    viewer = roles.client("viewer")
    assert viewer.post(path(system)).status_code == 403
    fake_llm.script(answer(*FULL))
    run(roles, system, as_role="owner")
    pending = by_key(listed(roles, system))
    one = pending["customer", None, "description"]["id"]

    assert viewer.get(path(system)).status_code == 403
    assert viewer.get(f"{path(system)}/items").status_code == 403
    for action, body in (
        ("accept", {"ids": [one]}),
        ("reject", {"ids": [one]}),
        ("accept-all", {}),
        ("accept-edited", {"id": one, "value": "Mine."}),
    ):
        assert viewer.post(f"{path(system)}/{action}", json=body).status_code == 403
    assert table_of(roles, system, "customer")["description"] is None  # pending is not shown
    assert roles.client("editor").post(f"{path(system)}/accept", json={"ids": [one]}).is_success
    seen = viewer.get(f"{system}/schema").json()
    customer = next(t for t in seen["tables"] if t["name"] == "customer")
    assert customer["description"] == "One row per customer."


def test_the_job_is_skipped_without_an_agent_model(roles: RoleClients, fake_llm):
    system = imported(roles)

    job = run(roles, system)

    assert job["status"] == "succeeded" and job["made"] == 0
    log = roles.client("editor").get(f"/api/v1/jobs/{job['job_id']}").json()["log"]
    assert "Skipped" in log
    assert chats(fake_llm) == []


# -- what the model receives under each AI privacy setting --------------------------------


def test_with_metadata_only_the_model_gets_no_statistics_and_no_tools(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer())

    run(roles, system)

    [call] = chats(fake_llm)
    assert list(call.tools) == []
    orders = shown(call)["sales.orders"]
    assert {c["name"]: c["data_type"] for c in orders["columns"]} == {
        "id": "integer",
        "status_code": "varchar(2)",
    }
    assert "rows" not in orders and all("profile" not in c for c in orders["columns"])


def test_profile_statistics_are_counts_and_percentages_only_when_shared(
    roles: RoleClients, scratch_source, model, fake_llm
):
    scratch_source["run"](
        "CREATE TABLE people (id integer PRIMARY KEY, email text, kind text);"
        "INSERT INTO people SELECT i, 'zz-' || i || '@leak.example', 'K' || (i % 2) "
        "FROM generate_series(1, 10) i;"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    people = ids(roles, system)["people"]
    assert profile_tables(roles, system, [people]).status_code == 202
    owner = roles.client("owner")
    assert use_models(owner, roles.workspace_id, model, share_profiles=True).status_code == 200
    fake_llm.calls.clear()
    fake_llm.script(answer())

    run(roles, system)

    [call] = chats(fake_llm)
    table = shown(call)["public.people"]
    columns = {c["name"]: c for c in table["columns"]}
    assert table["rows"] == 10
    assert set(columns["kind"]["profile"]) == {"null_pct", "distinct_pct"}
    assert columns["kind"]["profile"]["distinct_pct"] == 20.0
    assert "profile" not in columns["email"]  # a protected column
    text = json.dumps(shown(call))
    assert "leak.example" not in text and "top_values" not in text and "K0" not in text


def test_documents_are_searched_only_when_shared_and_indexed(roles: RoleClients, model, fake_llm):
    system = imported(roles)
    upload_document(roles, system)
    share_documents(roles, True)
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(tool_calls=(ToolCall("s1", "search_documents", {"query": "customer"}),)),
        answer({"table": "sales.customer", "description": "From the dictionary."}),
    )

    run(roles, system)

    first, second = chats(fake_llm)[:2]
    assert [t.name for t in first.tools] == ["search_documents"]
    [result] = [m for m in second.messages if m.role == "tool"]
    assert "national ID" in (result.content or "")

    share_documents(roles, False)
    fake_llm.calls.clear()
    fake_llm.script(answer())
    run(roles, system)
    assert list(chats(fake_llm)[0].tools) == []


def test_distinct_values_need_a_connection_and_shared_sample_rows(
    roles: RoleClients, scratch_source, model, fake_llm
):
    scratch_source["run"](
        "CREATE TABLE orders (id integer PRIMARY KEY, status_code text, email text);"
        "INSERT INTO orders SELECT i, CASE WHEN i % 2 = 0 THEN 'OP' ELSE 'CL' END, "
        "'zz-' || i || '@leak.example' FROM generate_series(1, 6) i;"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    fake_llm.script(answer())
    run(roles, system)
    assert list(chats(fake_llm)[0].tools) == []  # sample rows are not shared

    owner = roles.client("owner")
    assert use_models(owner, roles.workspace_id, model, share_samples=True).status_code == 200
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(
            tool_calls=(
                ToolCall("q1", "distinct_values", {"column": "public.orders.status_code"}),
                ToolCall("q2", "distinct_values", {"column": "public.orders.email"}),
            )
        ),
        answer(
            {
                "table": "public.orders",
                "columns": [{"column": "status_code", "description": "OP open, CL closed."}],
            }
        ),
    )

    job = run(roles, system)

    first, second = chats(fake_llm)[:2]
    assert [t.name for t in first.tools] == ["distinct_values"]
    results = {m.tool_call_id: m.content or "" for m in second.messages if m.role == "tool"}
    assert sorted(json.loads(results["q1"])["values"]) == ["CL", "OP"]
    assert "personal data" in results["q2"] and "leak.example" not in results["q2"]
    log = roles.client("editor").get(f"/api/v1/jobs/{job['job_id']}").json()["log"]
    assert "Source query" in log and "status_code" in log
    [made] = listed(roles, system)
    assert made["value"] == "OP open, CL closed."

    # Imported (no Connection): never offered, whatever the setting.
    other = imported(roles)
    fake_llm.calls.clear()
    fake_llm.script(answer())
    run(roles, other)
    assert list(chats(fake_llm)[0].tools) == []


def test_model_text_is_redacted_before_it_is_saved(roles: RoleClients, model, fake_llm):
    system = imported(roles)
    fake_llm.script(
        answer({"table": "sales.country", "description": "Ask zz-person@leak.example for codes."})
    )

    run(roles, system)

    [made] = listed(roles, system)
    assert "leak.example" not in made["value"] and "[redacted" in made["value"]


# -- a person's value wins -----------------------------------------------------------------


def test_accepting_never_overwrites_a_value_a_person_set_meanwhile(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    pending = by_key(listed(roles, system))
    description = pending["orders", None, "description"]
    name = pending["customer", "name", "description"]
    orders = table_of(roles, system, "orders")
    editor = roles.client("editor")
    assert editor.patch(
        f"{system}/tables/{orders['id']}",
        json={"version": orders["version"], "description": "A person's words."},
    ).is_success
    customer = table_of(roles, system, "customer")
    column = next(c for c in customer["columns"] if c["name"] == "name")
    assert editor.patch(
        f"{system}/tables/{customer['id']}/columns/{column['id']}",
        json={"version": column["version"], "description": "Typed by hand."},
    ).is_success

    accepted = editor.post(f"{path(system)}/accept", json={"ids": [description["id"]]})
    edited = editor.post(
        f"{path(system)}/accept-edited", json={"id": name["id"], "value": "Something else."}
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"decided": [], "skipped": [description["id"]]}
    assert edited.status_code == 409
    assert edited.json()["error"]["code"] == "suggestion_not_pending"
    assert table_of(roles, system, "orders")["description"] == "A person's words."
    after = next(c for c in table_of(roles, system, "customer")["columns"] if c["name"] == "name")
    assert after["description"] == "Typed by hand."
    expired = by_key(listed(roles, system, "expired"))
    assert ("orders", None, "description") in expired
    assert ("customer", "name", "description") in expired
    assert listed(roles, system, "accepted") == []


def test_a_persons_edit_expires_only_the_pending_suggestion_of_that_field(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    customer = table_of(roles, system, "customer")

    response = roles.client("editor").patch(
        f"{system}/tables/{customer['id']}",
        json={"version": customer["version"], "description": "Ours."},
    )

    assert response.status_code == 200, response.text
    assert list(by_key(listed(roles, system, "expired"))) == [("customer", None, "description")]
    pending = by_key(listed(roles, system))
    assert ("customer", None, "classification") in pending
    assert ("customer", "name", "description") in pending


def test_an_scd_hint_needs_a_master_or_reference_classification_when_accepted(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    pending = by_key(listed(roles, system))
    editor = roles.client("editor")

    # Accepted together, the classification goes first and the hint may follow.
    both = [pending["customer", None, f]["id"] for f in ("scd_hint", "classification")]
    response = editor.post(f"{path(system)}/accept", json={"ids": both})
    assert response.status_code == 200, response.text
    assert response.json()["skipped"] == []
    assert table_of(roles, system, "customer")["scd_hint"].startswith("Names change")

    # A classification that is no longer master or reference expires a pending hint.
    fake_llm.script(
        answer({"table": "sales.country", "classification": "reference", "scd_hint": "Fixed list."})
    )
    run(roles, system)
    country = table_of(roles, system, "country")
    changed = editor.patch(
        f"{system}/tables/{country['id']}",
        json={"version": country["version"], "classification": "log"},
    )
    assert changed.status_code == 200, changed.text
    assert ("country", None, "scd_hint") in by_key(listed(roles, system, "expired"))
    hint = by_key(listed(roles, system, "expired"))["country", None, "scd_hint"]
    refused = editor.post(f"{path(system)}/accept", json={"ids": [hint["id"]]})
    assert refused.json() == {"decided": [], "skipped": [hint["id"]]}
    assert table_of(roles, system, "country")["scd_hint"] is None


def test_an_scd_hint_alone_is_not_accepted_for_an_unclassified_table(
    roles: RoleClients, model, fake_llm
):
    system = imported(roles)
    fake_llm.script(answer(*FULL))
    run(roles, system)
    hint = by_key(listed(roles, system))["customer", None, "scd_hint"]

    response = roles.client("editor").post(f"{path(system)}/accept", json={"ids": [hint["id"]]})

    assert response.json() == {"decided": [], "skipped": [hint["id"]]}
    assert table_of(roles, system, "customer")["scd_hint"] is None
