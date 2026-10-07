"""The Source System dashboard (spec story 63) and Source Analysis progress (story 37).

Behaviour is driven through the HTTP API against a scratch source database: the summary's
numbers are computed from the Source Schema, profiles, relationships and PII findings, and
the Workspace stage-progress endpoint reports one Source Analysis entry per Source System.
"""

from __future__ import annotations

import pytest

from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_profiling import profile_tables
from tests.sources.test_relationship_inference import listed as relationships
from tests.sources.test_relationship_inference import run as infer
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source

SOURCE = """
CREATE TABLE customers (id integer PRIMARY KEY, email text);
CREATE TABLE orders (id integer PRIMARY KEY, customer_id integer);
CREATE TABLE notes (id integer PRIMARY KEY, body text);
CREATE TABLE audit_log (id integer PRIMARY KEY, body text);
INSERT INTO customers SELECT i, 'c' || i || '@x.com' FROM generate_series(1, 10) i;
INSERT INTO orders SELECT i, 1 + i % 10 FROM generate_series(1, 20) i;
INSERT INTO notes SELECT i, 'n' FROM generate_series(1, 5) i;
INSERT INTO audit_log SELECT i, 'a' FROM generate_series(1, 5) i;
"""


def summary(roles: RoleClients, system: str, as_role="viewer") -> dict:
    response = roles.client(as_role).get(f"{system}/summary")
    assert response.status_code == 200, response.text
    return response.json()


def progress(roles: RoleClients, as_role="viewer") -> list[dict]:
    response = roles.client(as_role).get(f"/api/v1/workspaces/{roles.workspace_id}/progress")
    assert response.status_code == 200, response.text
    return response.json()["source_analysis"]


def table(roles: RoleClients, system: str, name: str) -> dict:
    [found] = [t for t in schema(roles, system)["tables"] if t["name"] == name]
    return found


def describe(roles: RoleClients, system: str, t: dict) -> None:
    response = roles.client("editor").patch(
        f"{system}/tables/{t['id']}", json={"version": t["version"], "description": "Docs"}
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def analysed(roles: RoleClients, scratch_source) -> str:
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    return system


def test_a_system_without_a_snapshot_has_an_empty_dashboard(roles: RoleClients):
    system = add_system(roles)

    body = summary(roles, system)

    assert body["has_snapshot"] is False and body["status"] == "not_started"
    assert (body["table_count"], body["documented_pct"], body["profiled_pct"]) == (0, 0.0, 0.0)
    assert (body["relationships_found"], body["pii_found"]) == (0, 0)
    [entry] = progress(roles)
    assert entry["name"] == "Core" and entry["status"] == "not_started"


def test_the_numbers_come_from_the_schema_profiles_relationships_and_pii(
    roles: RoleClients, analysed: str
):
    body = summary(roles, analysed)
    assert body["has_snapshot"] is True and body["status"] == "in_progress"
    assert body["table_count"] == 4
    assert (body["documented_tables"], body["documented_pct"]) == (0, 0.0)
    assert (body["profiled_tables"], body["profiled_pct"]) == (0, 0.0)
    assert body["relationships_found"] == 0
    assert body["pii_found"] >= 1, "customers.email is suspected by name"

    for name in ("customers", "orders"):
        describe(roles, analysed, table(roles, analysed, name))
    ids = [table(roles, analysed, n)["id"] for n in ("customers", "orders", "notes")]
    assert profile_tables(roles, analysed, ids).status_code == 202
    infer(roles, analysed)

    body = summary(roles, analysed)
    assert (body["documented_tables"], body["documented_pct"]) == (2, 50.0)
    assert (body["profiled_tables"], body["profiled_pct"]) == (3, 75.0)
    found = [r for r in relationships(roles, analysed) if r["status"] != "rejected"]
    assert body["relationships_found"] == len(found) >= 1
    assert body["relationships_accepted"] == 0


def test_decisions_move_the_pii_and_relationship_counts(roles: RoleClients, analysed: str):
    infer(roles, analysed)
    before = summary(roles, analysed)
    editor = roles.client("editor")
    [finding] = [
        f
        for f in editor.get(f"{analysed}/pii-findings").json()["items"]
        if f["column"] == "email" and f["status"] == "suggested"
    ]
    relationship = relationships(roles, analysed)[0]

    assert editor.post(f"{analysed}/pii-findings/{finding['id']}/confirm").status_code == 200
    assert editor.post(f"{analysed}/relationships/{relationship['id']}/accept").status_code == 200
    after = summary(roles, analysed)
    assert after["pii_found"] == before["pii_found"] and after["pii_confirmed"] == 1
    assert after["relationships_accepted"] == 1

    assert editor.post(f"{analysed}/pii-findings/{finding['id']}/dismiss").status_code == 200
    assert editor.post(f"{analysed}/relationships/{relationship['id']}/reject").status_code == 200
    final = summary(roles, analysed)
    assert final["pii_found"] == before["pii_found"] - 1 and final["pii_confirmed"] == 0
    assert final["relationships_found"] == before["relationships_found"] - 1


def test_source_analysis_is_complete_once_everything_is_documented_profiled_and_reviewed(
    roles: RoleClients, analysed: str
):
    infer(roles, analysed)
    editor = roles.client("editor")
    tables = schema(roles, analysed)["tables"]
    for t in tables:
        describe(roles, analysed, t)
    assert profile_tables(roles, analysed, [t["id"] for t in tables]).status_code == 202
    queue = editor.get(f"{analysed}/pii-findings", params={"status": "suggested"})
    for f in queue.json()["items"]:
        editor.post(f"{analysed}/pii-findings/{f['id']}/dismiss")
    for r in relationships(roles, analysed, status="suggested"):
        editor.post(f"{analysed}/relationships/{r['id']}/reject")

    body = summary(roles, analysed)

    assert (body["documented_pct"], body["profiled_pct"]) == (100.0, 100.0)
    assert body["status"] == "complete"
    [entry] = progress(roles)
    assert entry["status"] == "complete" and entry["system_id"] == analysed.rsplit("/", 1)[1]


def test_every_member_reads_the_dashboard_and_others_do_not(roles: RoleClients, analysed: str):
    seen = {
        r: roles.client(r).get(f"{analysed}/summary").status_code
        for r in ("owner", "editor", "viewer")
    }
    assert seen == {"owner": 200, "editor": 200, "viewer": 200}
    assert roles.client("non_member").get(f"{analysed}/summary").status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    base = analysed.rsplit("/", 1)[0]
    assert roles.client("viewer").get(f"{base}/{missing}/summary").status_code == 404
