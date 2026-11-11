"""The Profiling page (spec §6.5, story 57a): every table's profiling status and findings,
and its data growth across Snapshots, through the HTTP API against a scratch source
database. The rules themselves are checked in ``test_profiling_overview_rules``."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_profiling import profile_tables, table_of

scratch_source = test_extraction.scratch_source

SOURCE = """
CREATE TABLE orders (
    id integer PRIMARY KEY,
    channel text,
    cancel_reason text,
    legacy_ref text,
    invoice_no text,
    national_id text
);
INSERT INTO orders
SELECT i,
       'WEB',
       CASE WHEN i % 5 = 0 THEN 'late' END,
       NULL,
       'INV-' || i,
       '29901011234567'
FROM generate_series(1, 10) i;
CREATE TABLE codes (code text UNIQUE);
INSERT INTO codes SELECT 'C' || i FROM generate_series(1, 10) i;
CREATE VIEW open_orders AS SELECT id, invoice_no FROM orders;
ANALYZE;
"""


def overview(roles: RoleClients, system: str, as_role="viewer"):
    return roles.client(as_role).get(f"{system}/profiling/overview")


def growth(roles: RoleClients, system: str, as_role="viewer"):
    return roles.client(as_role).get(f"{system}/profiling/growth")


def tables_by_name(body: dict) -> dict[str, dict]:
    return {t["name"]: t for t in body["tables"]}


def test_before_the_first_snapshot_there_is_nothing(roles: RoleClients):
    system = add_system(roles)

    body = overview(roles, system).json()
    assert body == {"latest_snapshot_at": None, "tables": []}
    assert growth(roles, system).json()["tables"] == []


def test_each_table_shows_its_status_and_findings(roles: RoleClients, scratch_source, clock):
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)

    before = tables_by_name(overview(roles, system).json())
    assert set(before) == {"orders", "codes", "open_orders"}
    assert before["orders"]["profiled_at"] is None and before["orders"]["findings"] == []
    assert before["orders"]["column_count"] == 6 and before["open_orders"]["kind"] == "view"

    ids = [table_of(roles, system, n)["id"] for n in ("orders", "codes", "open_orders")]
    clock.advance(timedelta(hours=1))
    assert profile_tables(roles, system, ids).status_code == 202

    response = overview(roles, system)
    assert response.status_code == 200, response.text
    tables = tables_by_name(response.json())
    orders = tables["orders"]
    assert orders["rows_sampled"] == 10 and orders["capped"] is False
    assert orders["profiled_at"] is not None and orders["out_of_date"] is False
    found = {f["column"]: f for f in orders["findings"]}
    assert {c: f["kind"] for c, f in found.items()} == {
        "channel": "constant",
        "cancel_reason": "mostly_null",
        "legacy_ref": "all_null",
        "invoice_no": "unique_not_key",
    }
    assert found["channel"]["value"] == "WEB"
    assert found["cancel_reason"]["null_pct"] == 80.0
    # A unique key and a view's unique column are not findings.
    assert tables["codes"]["findings"] == []
    assert tables["open_orders"]["findings"] == []

    # Profiled before a newer Snapshot: out of date.
    scratch_source["run"]("ALTER TABLE codes ADD COLUMN label text;")
    clock.advance(timedelta(hours=1))
    extract(roles, system)
    assert tables_by_name(overview(roles, system).json())["orders"]["out_of_date"] is True


def test_a_protected_column_shows_no_value(roles: RoleClients, scratch_source):
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    orders = table_of(roles, system, "orders")
    # Flag channel as sensitive; national_id is protected by its name already.
    channel = next(c for c in orders["columns"] if c["name"] == "channel")
    flagged = roles.client("editor").patch(
        f"{system}/tables/{orders['id']}/columns/{channel['id']}",
        json={"version": channel["version"], "is_sensitive": True},
    )
    assert flagged.status_code == 200, flagged.text
    profile_tables(roles, system, [orders["id"]])

    response = overview(roles, system)

    found = {f["column"] for f in tables_by_name(response.json())["orders"]["findings"]}
    assert "channel" not in found and "national_id" not in found
    assert "WEB" not in response.text and "29901011234567" not in response.text


def test_growth_follows_the_row_estimates_of_the_snapshots(
    roles: RoleClients, scratch_source, clock
):
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    assert growth(roles, system).json()["tables"] == []

    scratch_source["run"](
        "INSERT INTO orders (id) SELECT i FROM generate_series(11, 40) i; ANALYZE;"
    )
    # Sessions expire when idle: an hour is enough to measure growth.
    clock.advance(timedelta(hours=1))
    extract(roles, system)

    response = growth(roles, system)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["snapshots"]) == 2
    [orders] = body["tables"]
    assert (orders["name"], orders["first"], orders["latest"]) == ("orders", 10, 40)
    assert [p["row_estimate"] for p in orders["points"]] == [10, 40]
    assert orders["change_pct"] == 300.0 and orders["dropped_at"] is None
    assert orders["per_month"] == pytest.approx(30 * 24 * 30.44)
    assert {(t["name"], t["reason"]) for t in body["left_out"]} == {
        ("codes", "unchanged"),
        ("open_orders", "view"),
    }
    assert body["total_per_month"] == orders["per_month"]
    # The unchanged table counts in the totals, adding nothing per month.
    assert (body["total_first"], body["total_latest"]) == (10 + 10, 40 + 10)


def test_any_member_reads_the_page_but_only_in_their_workspace(roles: RoleClients):
    system = add_system(roles)
    for path in ("overview", "growth"):
        assert roles.client("viewer").get(f"{system}/profiling/{path}").status_code == 200
        assert roles.client("non_member").get(f"{system}/profiling/{path}").status_code in (
            403,
            404,
        )
