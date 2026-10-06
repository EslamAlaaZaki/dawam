"""Metadata extraction into Snapshots (spec stories 45, 46, §6.3, §7 "Source identity vs
Snapshots").

Behaviour is driven through the HTTP API: an editor starts an extraction (a background
job, run inline in tests) and reads the Snapshots it produced. Tests that change the
source use a scratch database of their own, never the shared sample source.

Deliberate storage-property checks read the module's own tables: the lifecycle state of
Source Objects (no endpoint shows it yet) and that a definition is stored once per
content hash.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import psycopg
import pytest
import sqlalchemy as sa

from dawam.modules.workspaces import WorkspaceService
from tests.roles import RoleClients
from tests.sample_source import SampleSource


def add_system(roles: RoleClients) -> str:
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Core", "code": "cbs"}
    )
    assert response.status_code == 201, response.text
    return f"/api/v1/workspaces/{roles.workspace_id}/systems/{response.json()['id']}"


def connect(roles: RoleClients, system: str, body: dict) -> None:
    response = roles.client("owner").put(f"{system}/connection", json=body)
    assert response.status_code in (200, 201), response.text


def extract(roles: RoleClients, system: str, as_role="editor") -> dict:
    """Start an extraction and return its (finished, as jobs run inline) job."""
    client = roles.client(as_role)
    started = client.post(f"{system}/extractions")
    assert started.status_code == 202, started.text
    job = client.get(f"/api/v1/jobs/{started.json()['job_id']}")
    assert job.status_code == 200, job.text
    return job.json()


def snapshots(roles: RoleClients, system: str, as_role="viewer") -> list[dict]:
    response = roles.client(as_role).get(f"{system}/snapshots")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def snapshot(roles: RoleClients, system: str, snapshot_id: str, as_role="viewer") -> dict:
    response = roles.client(as_role).get(f"{system}/snapshots/{snapshot_id}")
    assert response.status_code == 200, response.text
    return response.json()


def latest(roles: RoleClients, system: str) -> dict:
    [summary] = [s for s in snapshots(roles, system) if s["is_latest"]]
    return snapshot(roles, system, summary["id"])


def tables_of(content: dict) -> dict[tuple[str, str], dict]:
    return {(t["db_schema"], t["name"]): t for t in content["tables"]}


ScratchSource = Callable[..., dict]


@pytest.fixture
def scratch_source(fresh_database_url: str) -> Callable[..., dict]:
    """An empty source database of this test's own: ``run(sql)`` changes it and
    ``body(schemas)`` is a ``PUT .../connection`` body for it (as its superuser)."""
    url = sa.make_url(fresh_database_url)

    def run(statements: str) -> None:
        with psycopg.connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname=url.database,
            autocommit=True,
        ) as conn:
            conn.execute(statements)  # type: ignore[arg-type]

    def body(schemas=("public",)) -> dict:
        return {
            "engine": "postgresql",
            "host": url.host,
            "port": url.port,
            "database": url.database,
            "username": url.username,
            "password": url.password,
            "allowed_schemas": list(schemas),
        }

    return {"run": run, "body": body}  # type: ignore[return-value]


def test_an_editor_extracts_the_full_catalog_into_a_snapshot(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())

    job = extract(roles, system)

    assert (job["type"], job["status"], job["progress"]) == ("extract", "succeeded", 100)
    assert "7 tables and views" in job["log"] and "2 routines" in job["log"]
    [summary] = snapshots(roles, system)
    assert summary["is_latest"] and summary["origin"] == "connection"
    assert summary["job_id"] == job["id"]
    assert (summary["table_count"], summary["routine_count"]) == (7, 2)
    content = snapshot(roles, system, summary["id"])
    assert [s["name"] for s in content["db_schemas"]] == ["core", "crm"]
    tables = tables_of(content)
    assert set(tables) == {
        ("core", "accounts"),
        ("core", "branches"),
        ("core", "customer_balances"),
        ("core", "customers"),
        ("core", "transactions"),
        ("core", "عملاء_محليون"),
        ("crm", "contacts"),
    }

    customers = tables["core", "customers"]
    assert (customers["kind"], customers["comment"]) == ("table", "One row per bank customer")
    assert customers["view_definition"] is None
    assert customers["row_estimate"] is None or customers["row_estimate"] >= 0
    columns = {c["name"]: c for c in customers["columns"]}
    assert [c["name"] for c in customers["columns"]] == [
        "cust_no",
        "full_name",
        "national_id",
        "email",
        "phone",
        "branch_code",
    ]
    assert columns["cust_no"] | {"id": None} == {
        "id": None,
        "name": "cust_no",
        "status": None,
        "ordinal": 1,
        "data_type": "integer",
        "is_nullable": False,
        "is_pk": True,
        "default": None,
        "comment": None,
    }
    assert columns["national_id"]["comment"] == "National ID number"
    assert columns["email"]["is_nullable"] and not columns["email"]["is_pk"]
    balance = {c["name"]: c for c in tables["core", "accounts"]["columns"]}["balance"]
    assert (balance["data_type"], balance["default"]) == ("numeric(14,2)", "0")

    constraints = {c["name"]: c for c in customers["constraints"]}
    assert constraints["customers_pkey"]["type"] == "pk"
    fk = constraints["customers_branch_fk"]
    assert fk["type"] == "fk" and fk["columns"] == ["branch_code"]
    assert fk["ref_table_id"] == tables["core", "branches"]["id"]
    assert (fk["ref_db_schema"], fk["ref_table"], fk["ref_columns"]) == (
        "core",
        "branches",
        ["branch_code"],
    )
    unique = {c["name"]: c for c in tables["core", "branches"]["constraints"]}
    assert unique["branches_name_uq"]["type"] == "unique"
    indexes = {i["name"]: i for i in customers["indexes"]}
    assert indexes["customers_lower_email_idx"] == {
        "name": "customers_lower_email_idx",
        "columns": ["lower(email)"],
        "is_unique": False,
    }

    view = tables["core", "customer_balances"]
    assert view["kind"] == "view" and "JOIN" in view["view_definition"]
    routines = {r["name"]: r for r in content["routines"]}
    assert set(routines) == {"account_turnover", "close_account"}
    assert routines["account_turnover"]["kind"] == "function"
    assert routines["account_turnover"]["signature"] == "p_acct integer"
    assert "JOIN" in routines["account_turnover"]["definition"]
    assert routines["close_account"]["kind"] == "procedure"


def test_only_the_allowed_database_schemas_are_read(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body(allowed_schemas=["crm"]))

    extract(roles, system)

    content = latest(roles, system)
    assert [s["name"] for s in content["db_schemas"]] == ["crm"]
    assert set(tables_of(content)) == {("crm", "contacts")}
    assert content["routines"] == []
    assert "salaries" not in str(content) and "restricted" not in str(content)


def test_an_extraction_with_no_differences_creates_no_new_snapshot(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)

    again = extract(roles, system, as_role="owner")

    assert again["status"] == "succeeded"
    assert "No differences from the previous Snapshot" in again["log"]
    assert len(snapshots(roles, system)) == 1


def status_of(app, table: str, name: str) -> str:
    """Storage-property check: a Source Object's lifecycle state (no endpoint shows it)."""
    with app.state.engine.connect() as conn:
        return conn.scalar(
            sa.text(f"SELECT status FROM {table} WHERE name = :name"), {"name": name}
        )


def test_a_changed_source_gets_a_new_snapshot_and_keeps_identities(
    roles: RoleClients, scratch_source, app
):
    scratch_source["run"](
        "CREATE TABLE orders (id integer PRIMARY KEY, note text, legacy text);"
        "CREATE TABLE gone (id integer);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    first = latest(roles, system)

    scratch_source["run"](
        "ALTER TABLE orders ALTER COLUMN note TYPE varchar(20);"
        "ALTER TABLE orders DROP COLUMN legacy;"
        "ALTER TABLE orders ADD COLUMN placed_on date;"
        "DROP TABLE gone;"
    )
    extract(roles, system)

    listed = snapshots(roles, system)
    assert len(listed) == 2
    assert [s["is_latest"] for s in listed] == [True, False], "newest first"
    second = latest(roles, system)
    old_orders, new_orders = (
        tables_of(first)["public", "orders"],
        tables_of(second)["public", "orders"],
    )
    assert new_orders["id"] == old_orders["id"], "the table keeps its identity"
    old_cols = {c["name"]: c for c in old_orders["columns"]}
    new_cols = {c["name"]: c for c in new_orders["columns"]}
    assert new_cols["note"]["id"] == old_cols["note"]["id"], "a type change keeps identity"
    assert (old_cols["note"]["data_type"], new_cols["note"]["data_type"]) == (
        "text",
        "character varying(20)",
    )
    assert set(new_cols) == {"id", "note", "placed_on"}
    assert ("public", "gone") not in tables_of(second)
    assert snapshot(roles, system, listed[1]["id"]) == first | {"is_latest": False}, (
        "older Snapshots never change"
    )
    assert status_of(app, "src_tables", "gone") == "source_removed"
    assert status_of(app, "src_columns", "legacy") == "source_removed"
    assert status_of(app, "src_columns", "note") == "present"


def test_letter_case_differences_are_different_objects_in_postgresql(
    roles: RoleClients, scratch_source
):
    scratch_source["run"](
        'CREATE TABLE customer (id integer); CREATE TABLE "Customer" (id integer);'
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())

    extract(roles, system)

    tables = tables_of(latest(roles, system))
    assert tables["public", "customer"]["id"] != tables["public", "Customer"]["id"]


def test_narrowing_the_allowed_schemas_makes_objects_out_of_scope_not_removed(
    roles: RoleClients, scratch_source, app
):
    scratch_source["run"](
        "CREATE SCHEMA sales; CREATE TABLE sales.invoices (id integer);"
        "CREATE TABLE public.notes (id integer);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"](("public", "sales")))
    extract(roles, system)

    connect(roles, system, scratch_source["body"](("public",)))
    extract(roles, system)

    assert set(tables_of(latest(roles, system))) == {("public", "notes")}
    assert status_of(app, "src_db_schemas", "sales") == "out_of_scope"
    assert status_of(app, "src_tables", "invoices") == "out_of_scope"

    connect(roles, system, scratch_source["body"](("public", "sales")))
    extract(roles, system)

    assert status_of(app, "src_tables", "invoices") == "present"
    assert set(tables_of(latest(roles, system))) == {("public", "notes"), ("sales", "invoices")}


def test_view_and_routine_definitions_are_stored_once_per_content_hash(
    roles: RoleClients, scratch_source, app
):
    scratch_source["run"](
        "CREATE TABLE t (id integer);"
        "CREATE VIEW v AS SELECT id FROM t;"
        "CREATE FUNCTION f() RETURNS integer LANGUAGE sql AS 'SELECT 1';"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    scratch_source["run"]("ALTER TABLE t ADD COLUMN extra text;")

    extract(roles, system)

    listed = snapshots(roles, system)
    assert len(listed) == 2
    views = [tables_of(snapshot(roles, system, s["id"]))["public", "v"] for s in listed]
    assert views[0]["view_definition"] == views[1]["view_definition"]
    with app.state.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM definition_texts")) == 2


def test_extraction_needs_a_connection(roles: RoleClients):
    system = add_system(roles)

    response = roles.client("editor").post(f"{system}/extractions")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "connection_missing"


def test_a_failed_extraction_says_why_and_creates_no_snapshot(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body(password="wrong"))

    job = extract(roles, system)

    assert job["status"] == "failed"
    assert job["error"] == "The database rejected the username or password."
    assert snapshots(roles, system) == []


def test_extraction_is_refused_in_an_archived_workspace(
    roles: RoleClients, sample_source: SampleSource, app
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    state = app.state
    WorkspaceService(state.engine, clock=state.services.clock).archive(
        roles.user("owner"), roles.workspace_id
    )

    refused = roles.client("owner").post(f"{system}/extractions")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "workspace_archived"
    assert len(snapshots(roles, system, as_role="owner")) == 1, "Snapshots stay readable"


def test_a_new_snapshot_is_recorded_in_the_activity_feed(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())

    extract(roles, system)

    feed = roles.client("viewer").get(f"/api/v1/workspaces/{roles.workspace_id}/activity")
    [event] = [e for e in feed.json()["items"] if e["verb"] == "snapshot.created"]
    assert event["actor"]["user_id"] == str(roles.user("editor").id)
    assert event["object_label"] == "Core"


def test_a_2000_table_schema_extracts_in_under_five_minutes(roles: RoleClients, scratch_source):
    """Spec §9: extract metadata for a 2 000-table database in < 5 min."""
    for first in range(1, 2001, 250):  # in batches: one transaction would run out of locks
        scratch_source["run"](
            f"""
            DO $$
            BEGIN
              FOR i IN {first}..{first + 249} LOOP
                EXECUTE format(
                  'CREATE TABLE t%s (id bigint PRIMARY KEY, code varchar(20) NOT NULL UNIQUE,'
                  ' name text, amount numeric(14, 2) DEFAULT 0, created_at timestamptz,'
                  ' parent_id bigint, flag boolean, note text)', i);
                EXECUTE format('CREATE INDEX t%s_parent_idx ON t%s (parent_id)', i, i);
                EXECUTE format('COMMENT ON TABLE t%s IS %L', i, 'generated table ' || i);
              END LOOP;
            END
            $$;
            """
        )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())

    started = time.monotonic()
    job = extract(roles, system)
    elapsed = time.monotonic() - started

    assert job["status"] == "succeeded", job["error"]
    [summary] = snapshots(roles, system)
    assert (summary["table_count"], summary["column_count"]) == (2000, 16000)
    assert elapsed < 300, f"extraction took {elapsed:.0f} s"
