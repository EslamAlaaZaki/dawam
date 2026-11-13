"""Detect PII: the value scan and the AI PII detection side by side (spec §6.12, #601).

Driven through the HTTP API with jobs run inline; the model is the scripted fake provider
behind a registered agent model, so ``fake_llm.calls`` shows exactly what the AI saw. The
AI reads metadata only: the tests plant sentinel values in the source and in the stored
profile and prove none reaches the prompt, the job or the findings.
"""

from __future__ import annotations

import json

import pytest

from dawam.modules.llm import Reply, ToolCall
from tests.llm.test_roles_budgets import register, use_models
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_pii_findings import decide, findings, tab
from tests.sources.test_pii_value_scan import SENTINELS, seeded_system
from tests.sources.test_schema_browser import schema
from tests.sources.test_schema_import import csvs, upload

scratch_source = test_extraction.scratch_source

PEOPLE = {"contact": "c1", "id": "c2", "note": "c3", "ref": "c4"}
"""The column refs the AI is shown when only ``people`` is chosen: by name."""


@pytest.fixture
def model(admin_client, fake_llm, roles: RoleClients):
    assert roles.workspace_id  # created first, while there is no model to choose
    agent = register(admin_client, "pii-model")
    assert use_models(roles.client("owner"), roles.workspace_id, agent).status_code == 200
    fake_llm.calls.clear()  # the connection test is not what the tests look at
    return agent


def answer(*findings: dict) -> Reply:
    return Reply(text=json.dumps({"findings": list(findings)}))


def detect(roles: RoleClients, system: str, table_ids: list[str], as_role="editor") -> dict:
    started = roles.client(as_role).post(f"{system}/pii-scans", json={"table_ids": table_ids})
    assert started.status_code == 202, started.text
    return started.json()


def job(roles: RoleClients, job_id: str) -> dict:
    response = roles.client("editor").get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def chats(fake_llm):
    return [c for c in fake_llm.calls if c.kind == "chat"]


def prompt(call) -> str:
    return "\n".join(m.content or "" for m in call.messages)


def shown(call) -> dict:
    """The metadata the AI was shown (the JSON inside the user turn's <data> tags)."""
    text = next(m.content for m in call.messages if m.role == "user")
    return json.loads(text.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])


def imported(roles: RoleClients, columns: str) -> tuple[str, dict[str, str]]:
    """A Source System from a Schema Import: tables but no Connection."""
    system = add_system(roles)
    upload(
        roles,
        system,
        csvs(
            tables="schema,table,kind,row_count\nsales,customer,table,5\n",
            columns="schema,table,column,data_type\n" + columns,
        ),
    )
    return system, {t["name"]: t["id"] for t in schema(roles, system)["tables"]}


# -- findings per source -----------------------------------------------------------------


def test_name_value_and_ai_rows_of_one_rule_coexist_on_a_column(
    roles: RoleClients, scratch_source, model, fake_llm
):
    scratch_source["run"]("CREATE TABLE staff (id integer PRIMARY KEY, email text);")
    for i in range(4):
        scratch_source["run"](f"INSERT INTO staff VALUES ({i}, 'zz-{i}@leak.example');")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    table = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}["staff"]
    fake_llm.script(answer({"column": "c1", "rule": "email", "confidence": 0.95, "evidence": "e"}))

    detect(roles, system, [table])

    rows = [f for f in findings(roles, system) if f["column"] == "email"]
    assert len(rows) == 3 and {f["rule"] for f in rows} == {"email"}
    column = tab(roles, system, "suggested")["email"]
    by_source = {r["source"]: r for r in column["rules"]}
    assert set(by_source) == {"name", "value", "ai"}
    assert by_source["ai"]["confidence"] == pytest.approx(0.95)
    assert by_source["name"]["confidence"] < by_source["value"]["confidence"]  # untouched


# -- starting it -------------------------------------------------------------------------


def test_detect_pii_queues_the_value_scan_and_the_ai_detection_together(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system, tables = seeded_system(roles, scratch_source)
    fake_llm.script(answer())

    started = detect(roles, system, [tables["people"]])

    value, ai = job(roles, started["job_id"]), job(roles, started["ai_job_id"])
    assert (value["type"], value["status"]) == ("pii_scan", "succeeded")
    assert (ai["type"], ai["status"], ai["title"]) == (
        "pii_ai_detect",
        "succeeded",
        "AI PII detection: Core",
    )


def test_without_a_connection_only_the_ai_detection_is_queued(roles: RoleClients, model, fake_llm):
    system, tables = imported(roles, "sales,customer,id,integer\nsales,customer,cust_ref,text\n")
    fake_llm.script(
        answer({"column": "c1", "rule": "national_id", "confidence": 0.7, "evidence": "ref"})
    )

    started = detect(roles, system, [tables["customer"]])

    assert started["job_id"] is None and started["status"] is None
    assert job(roles, started["ai_job_id"])["status"] == "succeeded"
    column = tab(roles, system, "suggested")["cust_ref"]
    assert [(r["source"], r["rule"]) for r in column["rules"]] == [("ai", "national_id")]
    assert column["category"] == "direct_identifier"


def test_the_ai_detection_runs_when_the_value_scan_fails(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system, tables = seeded_system(roles, scratch_source)
    scratch_source["run"]("DROP TABLE people;")  # the value scan cannot sample it any more
    fake_llm.script(
        answer({"column": PEOPLE["ref"], "rule": "national_id", "confidence": 0.8, "evidence": ""})
    )

    started = detect(roles, system, [tables["people"]])

    assert job(roles, started["job_id"])["status"] == "failed"
    assert job(roles, started["ai_job_id"])["status"] == "succeeded"
    assert [(f["column"], f["rule"]) for f in findings(roles, system)] == [("ref", "national_id")]


def test_the_ai_detection_is_skipped_without_an_agent_model(
    roles: RoleClients, scratch_source, fake_llm
):
    system, tables = seeded_system(roles, scratch_source)

    started = detect(roles, system, [tables["people"]])

    ai = job(roles, started["ai_job_id"])
    assert ai["status"] == "succeeded" and "Skipped" in ai["log"]
    assert chats(fake_llm) == []
    sources = {r["source"] for c in tab(roles, system, "suggested").values() for r in c["rules"]}
    assert "ai" not in sources
    assert job(roles, started["job_id"])["status"] == "succeeded"


def test_only_owners_and_editors_detect_pii(roles: RoleClients, scratch_source, model):
    system, tables = seeded_system(roles, scratch_source)
    body = {"table_ids": [tables["people"]]}

    assert roles.client("viewer").post(f"{system}/pii-scans", json=body).status_code == 403
    assert roles.client("owner").post(f"{system}/pii-scans", json=body).status_code == 202


# -- what the AI sees --------------------------------------------------------------------


def test_the_ai_sees_metadata_findings_and_rules_and_never_a_value(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system, tables = seeded_system(roles, scratch_source)
    owner = roles.client("owner")
    people = tables["people"]
    # Profile values (min, max, top values) are stored for the table: none may reach the AI.
    put = owner.put(f"{system}/tables/{people}/profiling-settings", json={"enabled": True})
    assert put.status_code == 200, put.text
    assert owner.post(f"{system}/profiling", json={"table_ids": [people]}).status_code == 202
    rules = f"/api/v1/workspaces/{roles.workspace_id}/pii-rules"
    custom = owner.post(
        rules, json={"name": "staff_no", "keywords": ["staffno"], "category": "quasi_identifier"}
    )
    assert custom.status_code == 201, custom.text
    off = owner.patch(f"{rules}/built-in/religion", json={"enabled": False})
    assert off.status_code == 200, off.text
    fake_llm.script(
        answer(
            {
                "column": PEOPLE["note"],
                "rule": "health",
                "confidence": 0.6,
                "evidence": "Free text; reach me at zz-sentinel-0@leak.example",
            }
        )
    )

    started = detect(roles, system, [people])

    [call] = chats(fake_llm)
    seen = shown(call)
    [table] = seen["tables"]
    assert table["table"] == "public.people"
    columns = {c["name"]: c for c in table["columns"]}
    assert set(columns) == {"id", "ref", "contact", "note"}
    assert columns["ref"]["data_type"] == "text" and columns["id"]["primary_key"] is True
    assert [(c["type"], c["columns"]) for c in table["constraints"]] == [("pk", ["id"])]
    # The value scan ran first: its findings are context, with their source and status.
    assert {(f["source"], f["rule"], f["status"]) for f in columns["ref"]["findings"]} == {
        ("value", "national_id", "suggested")
    }
    catalogue = {r["id"]: r["category"] for r in seen["rules"]}
    assert catalogue["custom:staff_no"] == "quasi_identifier"
    assert catalogue["email"] == "direct_identifier" and "religion" not in catalogue
    # Nothing of the data: not the source's rows, not the stored profile.
    text = prompt(call)
    ai = job(roles, started["ai_job_id"])
    stored = [f["evidence"] for f in findings(roles, system)]
    for value in SENTINELS:
        assert value not in text, value
        assert value not in json.dumps(ai), value
        assert all(value not in e for e in stored), value
    assert "[redacted: email]" in tab(roles, system, "suggested")["note"]["rules"][0]["evidence"]


def test_what_the_server_cannot_trust_is_dropped(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system, tables = seeded_system(roles, scratch_source)
    fake_llm.script(
        answer(
            {"column": PEOPLE["ref"], "rule": "national_id", "confidence": 1.7, "evidence": "ok"},
            {"column": "c99", "rule": "email", "confidence": 0.9, "evidence": "unknown column"},
            {"column": "c5", "rule": "email", "confidence": 0.9, "evidence": "places: not chosen"},
            {"column": PEOPLE["note"], "rule": "made_up", "confidence": 0.9, "evidence": "rule"},
            {"column": PEOPLE["contact"], "rule": "email", "confidence": 0.4, "evidence": "low"},
        )
    )

    started = detect(roles, system, [tables["people"]])

    ai = [f for f in findings(roles, system) if f["evidence"] == "ok"]
    assert [(f["column"], f["rule"], f["confidence"]) for f in ai] == [("ref", "national_id", 1.0)]
    log = job(roles, started["ai_job_id"])["log"]
    assert "Reviewed 1 tables, 4 columns; 1 new findings, 0 updated, 4 dropped." in log
    assert "public.places" not in json.dumps(shown(chats(fake_llm)[0]))


def test_no_finding_is_a_normal_outcome(roles: RoleClients, scratch_source, model, fake_llm):
    system, tables = seeded_system(roles, scratch_source)
    fake_llm.script(answer())

    started = detect(roles, system, [tables["places"]])

    ai = job(roles, started["ai_job_id"])
    assert ai["status"] == "succeeded"
    assert "0 new findings, 0 updated, 0 dropped" in ai["log"]
    assert findings(roles, system) == []


def test_an_ai_finding_sends_a_rejected_column_back_by_the_reopen_rule(
    roles: RoleClients, model, fake_llm
):
    system, tables = imported(roles, "sales,customer,id,integer\nsales,customer,email,text\n")
    email = tab(roles, system, "suggested")["email"]
    assert decide(roles, system, email["id"], "reject").status_code == 200
    customer = [tables["customer"]]
    item = {"column": "c1", "rule": "email", "evidence": "the AI"}

    # A rule the AI never matched before: back to Suggested.
    fake_llm.script(answer({**item, "confidence": 0.7}))
    detect(roles, system, customer)
    back = tab(roles, system, "suggested")["email"]
    assert back["rejected_before_by"] is not None
    assert {r["source"] for r in back["rules"]} == {"name", "ai"}

    # Rejected again: the same confidence keeps it rejected, a higher one reopens it.
    assert decide(roles, system, email["id"], "reject").status_code == 200
    fake_llm.script(answer({**item, "confidence": 0.7}))
    detect(roles, system, customer)
    assert "email" in tab(roles, system, "rejected")
    fake_llm.script(answer({**item, "confidence": 0.9}))
    detect(roles, system, customer)
    assert "email" in tab(roles, system, "suggested")


# -- Documents ---------------------------------------------------------------------------


def share_documents(roles: RoleClients, on: bool) -> None:
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={"internal_only": False, "share_documents": on},
    )
    assert response.status_code == 200, response.text


DOCUMENT = b"# Customer\ncust_ref is the national ID."


def upload_document(roles: RoleClients, system: str) -> None:
    response = roles.client("owner").post(
        f"{system}/files",
        files={"file": ("dictionary.md", DOCUMENT, "text/markdown")},
    )
    assert response.status_code == 201, response.text


def offered(fake_llm) -> list[str]:
    return [t.name for t in chats(fake_llm)[0].tools]


def test_no_documents_tool_without_indexed_documents(roles: RoleClients, model, fake_llm):
    system, tables = imported(roles, "sales,customer,cust_ref,text\n")
    share_documents(roles, True)
    fake_llm.script(answer())

    detect(roles, system, [tables["customer"]])

    assert offered(fake_llm) == []


def test_no_documents_tool_when_the_workspace_does_not_share_documents(
    roles: RoleClients, model, fake_llm
):
    system, tables = imported(roles, "sales,customer,cust_ref,text\n")
    upload_document(roles, system)
    share_documents(roles, False)
    fake_llm.calls.clear()
    fake_llm.script(answer())

    detect(roles, system, [tables["customer"]])

    assert offered(fake_llm) == []
    assert "dictionary.md" not in prompt(chats(fake_llm)[0])


def test_the_ai_may_search_indexed_documents_when_the_workspace_shares_them(
    roles: RoleClients, model, fake_llm
):
    system, tables = imported(roles, "sales,customer,cust_ref,text\n")
    upload_document(roles, system)
    share_documents(roles, True)
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(tool_calls=(ToolCall("s1", "search_documents", {"query": "cust_ref"}),)),
        answer({"column": "c1", "rule": "national_id", "confidence": 0.9, "evidence": "doc"}),
    )

    detect(roles, system, [tables["customer"]])

    first, second = chats(fake_llm)[:2]
    assert [t.name for t in first.tools] == ["search_documents"]
    [result] = [m for m in second.messages if m.role == "tool"]
    assert "national ID" in (result.content or "")
    column = tab(roles, system, "suggested")["cust_ref"]
    assert [(r["source"], r["rule"]) for r in column["rules"]] == [("ai", "national_id")]
