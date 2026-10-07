"""Value-based PII scan (spec §6.12, stories 131, 132).

Scoring is unit-tested on its own; the job is driven through the HTTP API (an editor
starts a scan on selected tables, a background job run inline in tests) against a scratch
source database. One test reads every table of the app database and the job record to
prove no sampled value is kept anywhere.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from dawam.modules.sources.internal.pii_scan import (
    MIN_SAMPLED_VALUES,
    REVIEW_THRESHOLD,
    combine,
    score_column,
)
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_pii_findings import by_column, findings
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""

NO_SUCH = "00000000-0000-0000-0000-000000000000"

NATIONAL_IDS = ["1000000008", "1234567897", "1087654321", "1000000016"]
EMAILS = [f"zz-sentinel-{i}@leak.example" for i in range(4)]
SENTINELS = [*NATIONAL_IDS, *EMAILS, "free text sentinel one", "free text sentinel two"]


# -- scoring ---------------------------------------------------------------------------


def test_a_column_of_valid_ids_is_found_with_only_counts_as_evidence():
    found = score_column("ref", [*NATIONAL_IDS, "1000000009"])

    assert found is not None
    assert (found.rule, found.category, found.matched, found.total) == (
        "national_id",
        "direct_identifier",
        4,
        5,
    )
    assert found.confidence == pytest.approx(0.8)
    assert not any(value in found.evidence for value in NATIONAL_IDS)
    assert "80%" in found.evidence


def test_a_matching_name_raises_the_confidence():
    values = [*NATIONAL_IDS[:3], "1000000009", "12"]
    without = score_column("ref", values)
    named = score_column("national_id", values)

    assert without is not None and named is not None
    assert named.confidence > without.confidence
    assert named.confidence == pytest.approx(combine(0.9, 0.6))
    assert "name rule" in named.evidence


def test_a_weak_pattern_alone_is_not_a_finding_but_is_with_its_name():
    numbers = ["1010123456", "4030987654", "2050111222", "7000000001"]

    assert score_column("order_ref", numbers) is None
    named = score_column("cr_number", numbers)
    assert named is not None and named.rule == "commercial_registration"
    assert named.confidence >= REVIEW_THRESHOLD


def test_dates_of_birth_need_a_date_of_birth_name():
    dates = [date(1990, 1, 1), date(1985, 6, 7), date(2001, 12, 31)]

    assert score_column("created_at", dates, today=date(2026, 10, 7)) is None
    found = score_column("date_of_birth", dates, today=date(2026, 10, 7))
    assert found is not None and found.rule == "birth_date"


def test_the_most_confident_rule_wins_a_column():
    found = score_column("anything", NATIONAL_IDS)  # also 10 digits, a weak CR pattern

    assert found is not None and found.rule == "national_id"


@pytest.mark.parametrize(
    "values",
    [
        ["hello", "world", "plain text", "more text"],
        [None, None, None, None],
        NATIONAL_IDS[: MIN_SAMPLED_VALUES - 1],
        ["1000000008", "x", "y", "z", "w", "v"],  # under the minimum match ratio
    ],
)
def test_nothing_is_found_without_enough_matching_values(values):
    assert score_column("notes", values) is None


# -- the job -----------------------------------------------------------------------------


def seeded_system(roles: RoleClients, scratch_source) -> tuple[str, dict[str, str]]:
    """An extracted Source System over a scratch table whose column names say nothing."""
    scratch_source["run"](
        "CREATE TABLE people (id integer PRIMARY KEY, ref text, contact text, note text);"
        "CREATE TABLE places (id integer PRIMARY KEY, code text);"
    )
    for i in range(4):
        scratch_source["run"](
            f"INSERT INTO people VALUES ({i + 1}, '{NATIONAL_IDS[i]}', '{EMAILS[i]}', "
            f"'{'free text sentinel one' if i % 2 else 'free text sentinel two'}');"
            f"INSERT INTO places VALUES ({i + 1}, 'zone-{i}');"
        )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    tables = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}
    return system, tables


def scan(roles: RoleClients, system: str, table_ids: list[str], as_role="editor", **extra) -> dict:
    client = roles.client(as_role)
    started = client.post(f"{system}/pii-scans", json={"table_ids": table_ids, **extra})
    assert started.status_code == 202, started.text
    job = client.get(f"/api/v1/jobs/{started.json()['job_id']}")
    assert job.status_code == 200, job.text
    return job.json()


def test_a_scan_finds_pii_behind_vague_column_names_and_queues_it(
    roles: RoleClients, scratch_source
):
    system, tables = seeded_system(roles, scratch_source)
    assert findings(roles, system) == []

    job = scan(roles, system, [tables["people"]])

    assert (job["type"], job["status"], job["progress"]) == ("pii_scan", "succeeded", 100)
    found = by_column(findings(roles, system, status="suggested"))
    assert set(found) == {("people", "ref"), ("people", "contact")}
    assert found[("people", "ref")]["rule"] == "national_id"
    assert found[("people", "ref")]["category"] == "direct_identifier"
    assert found[("people", "contact")]["rule"] == "email"
    for finding in found.values():
        assert finding["confidence"] >= 0.5
        assert finding["status"] == "suggested" and finding["is_protected"] is True
        assert "match ratio 100%" in finding["evidence"]


def test_only_the_selected_tables_are_scanned(roles: RoleClients, scratch_source):
    system, tables = seeded_system(roles, scratch_source)

    scan(roles, system, [tables["places"]])

    assert findings(roles, system) == []


def test_a_name_finding_gains_confidence_and_keeps_its_decision(roles: RoleClients, scratch_source):
    scratch_source["run"]("CREATE TABLE staff (id integer PRIMARY KEY, email text);")
    for i in range(4):
        scratch_source["run"](f"INSERT INTO staff VALUES ({i}, '{EMAILS[i]}');")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    [before] = findings(roles, system)
    roles.client("editor").post(f"{system}/pii-findings/{before['id']}/dismiss")
    table = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}["staff"]

    scan(roles, system, [table])

    [after] = findings(roles, system)
    assert after["id"] == before["id"] and after["status"] == "dismissed"
    assert after["confidence"] > before["confidence"]
    assert "sampled values" in after["evidence"]


def test_the_sample_size_caps_the_rows_read(roles: RoleClients, scratch_source):
    system, tables = seeded_system(roles, scratch_source)

    scan(roles, system, [tables["people"]], sample_size=3)

    assert "match ratio 100%" in by_column(findings(roles, system))[("people", "ref")]["evidence"]
    [ref] = [f for f in findings(roles, system) if f["column"] == "ref"]
    assert "3 of 3" in ref["evidence"]


def test_no_sampled_value_is_stored_logged_or_in_the_job_output(
    roles: RoleClients, scratch_source, app: FastAPI, caplog: pytest.LogCaptureFixture
):
    system, tables = seeded_system(roles, scratch_source)

    with caplog.at_level(logging.DEBUG):
        job = scan(roles, system, [tables["people"], tables["places"]])

    assert job["status"] == "succeeded"
    listed = roles.client("editor").get(f"/api/v1/workspaces/{roles.workspace_id}/jobs")
    haystack = [str(job), caplog.text, str(listed.json())]
    with app.state.engine.connect() as conn:
        for table in sa.inspect(conn).get_table_names():
            rows = conn.execute(sa.text(f'SELECT row_to_json(t)::text FROM "{table}" t'))
            haystack.extend(row[0] for row in rows)
    text = "\n".join(haystack)
    for value in SENTINELS:
        assert value not in text, value


def test_the_scan_needs_a_live_connection(roles: RoleClients):
    system = add_system(roles)

    response = roles.client("editor").post(f"{system}/pii-scans", json={"table_ids": [NO_SUCH]})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "connection_missing"


def test_the_scan_needs_known_tables_and_a_valid_sample_size(roles: RoleClients, scratch_source):
    editor = roles.client("editor")
    system, tables = seeded_system(roles, scratch_source)
    unknown = editor.post(f"{system}/pii-scans", json={"table_ids": [NO_SUCH]})
    empty = editor.post(f"{system}/pii-scans", json={"table_ids": []})
    too_big = editor.post(
        f"{system}/pii-scans", json={"table_ids": [tables["people"]], "sample_size": 10**9}
    )
    assert unknown.status_code == 404
    assert empty.status_code == 422
    assert too_big.status_code == 422


def test_only_owners_and_editors_start_a_scan(roles: RoleClients, scratch_source):
    system, tables = seeded_system(roles, scratch_source)
    body = {"table_ids": [tables["people"]]}

    assert roles.client("viewer").post(f"{system}/pii-scans", json=body).status_code == 403
    assert roles.client("owner").post(f"{system}/pii-scans", json=body).status_code == 202
