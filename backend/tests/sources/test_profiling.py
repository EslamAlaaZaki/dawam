"""Profiling (spec §6.5, stories 55-57).

Behaviour is driven through the HTTP API against a scratch source database: an editor
profiles tables (a background job, run inline in tests), an owner switches top-N on per
table, and any member reads the profiles. Pattern detection and type kinds are checked
directly: they are pure.
"""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.profiling import detect_patterns, kind_of
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_schema_browser import schema
from tests.sources.test_schema_import import csvs, upload

scratch_source = test_extraction.scratch_source

SOURCE = """
CREATE TABLE people (
    id integer PRIMARY KEY,
    city text,
    email text,
    phone text,
    score integer,
    national_id text,
    notes text
);
INSERT INTO people
SELECT i,
       (ARRAY['Cairo', 'Giza', 'Alexandria'])[1 + i % 3],
       'user' || i || '@example.com',
       '+2010' || (10000000 + i),
       CASE WHEN i % 4 = 0 THEN NULL ELSE i END,
       '2990101' || (1000000 + i),
       'free text ' || i
FROM generate_series(1, 20) i;
"""


def table_of(roles: RoleClients, system: str, name: str = "people") -> dict:
    [table] = [t for t in schema(roles, system)["tables"] if t["name"] == name]
    return table


def profile_tables(roles: RoleClients, system: str, tables: list[str], as_role="editor", **body):
    return roles.client(as_role).post(f"{system}/profiling", json={"table_ids": tables, **body})


def profile_of(roles: RoleClients, system: str, table_id: str, as_role="viewer") -> dict:
    response = roles.client(as_role).get(f"{system}/tables/{table_id}/profile")
    assert response.status_code == 200, response.text
    return response.json()


def columns_of(profile: dict) -> dict[str, dict]:
    return {c["name"]: c for c in profile["columns"]}


@pytest.fixture
def profiled(roles: RoleClients, scratch_source):
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    return system, table_of(roles, system)["id"]


def run(roles: RoleClients, system: str, table_id: str, **body) -> dict:
    started = profile_tables(roles, system, [table_id], **body)
    assert started.status_code == 202, started.text
    job = roles.client("editor").get(f"/api/v1/jobs/{started.json()['job_id']}").json()
    assert job["type"] == "profile"
    return job


def test_an_editor_profiles_a_table_and_any_member_reads_it(roles: RoleClients, profiled):
    system, table_id = profiled

    job = run(roles, system, table_id)

    assert job["status"] == "succeeded" and job["progress"] == 100
    profile = profile_of(roles, system, table_id)
    assert profile["row_count"] == 20 and profile["top_n_enabled"] is False
    columns = columns_of(profile)
    score = columns["score"]["profile"]
    assert score["row_count"] == 20 and score["sampled"] is False
    assert score["null_pct"] == 25.0
    assert score["distinct_count"] == 15
    assert (score["min"], score["max"]) == ("1", "19")
    city = columns["city"]["profile"]
    assert city["distinct_count"] == 3
    assert (city["min"], city["max"]) == ("Alexandria", "Giza")
    assert city["max_len"] == 10 and 4 < city["avg_len"] < 10
    assert columns["email"]["profile"]["patterns"] == ["email"]
    assert columns["phone"]["profile"]["patterns"] == ["phone"]
    assert columns["notes"]["profile"]["patterns"] == []


def test_top_values_are_off_until_an_owner_switches_them_on_for_the_table(
    roles: RoleClients, profiled
):
    system, table_id = profiled
    run(roles, system, table_id)
    assert columns_of(profile_of(roles, system, table_id))["city"]["profile"]["top_values"] is None

    editor = roles.client("editor").put(
        f"{system}/tables/{table_id}/profiling-settings", json={"enabled": True}
    )
    assert editor.status_code == 403
    owner = roles.client("owner").put(
        f"{system}/tables/{table_id}/profiling-settings", json={"enabled": True}
    )
    assert owner.status_code == 200 and owner.json()["top_n_enabled"] is True
    run(roles, system, table_id)

    city = columns_of(profile_of(roles, system, table_id))["city"]["profile"]
    assert [(v["value"], v["count"]) for v in city["top_values"]] == [
        ("Alexandria", 7),
        ("Giza", 7),
        ("Cairo", 6),
    ]

    roles.client("owner").put(
        f"{system}/tables/{table_id}/profiling-settings", json={"enabled": False}
    )
    after = columns_of(profile_of(roles, system, table_id))["city"]["profile"]
    assert after["top_values"] is None


def test_protected_columns_never_get_min_max_or_top_values(roles: RoleClients, profiled):
    system, table_id = profiled
    roles.client("owner").put(
        f"{system}/tables/{table_id}/profiling-settings", json={"enabled": True}
    )
    columns = columns_of(profile_of(roles, system, table_id))
    # national_id is suggested PII by its name; flag notes as sensitive by hand.
    assert columns["national_id"]["is_protected"] is True
    notes = columns["notes"]
    flagged = roles.client("editor").patch(
        f"{system}/tables/{table_id}/columns/{notes['column_id']}",
        json={"version": table_of(roles, system)["columns"][-1]["version"], "is_sensitive": True},
    )
    assert flagged.status_code == 200, flagged.text

    run(roles, system, table_id)

    columns = columns_of(profile_of(roles, system, table_id))
    for name in ("national_id", "notes"):
        stats = columns[name]["profile"]
        assert columns[name]["is_protected"] is True
        assert stats["min"] is None and stats["max"] is None and stats["top_values"] is None
        assert stats["distinct_count"] == 20 and stats["row_count"] == 20
    assert columns["city"]["profile"]["min"] == "Alexandria"
    assert columns["city"]["profile"]["top_values"]


def test_a_column_protected_after_profiling_is_redacted_when_read(roles: RoleClients, profiled):
    system, table_id = profiled
    roles.client("owner").put(
        f"{system}/tables/{table_id}/profiling-settings", json={"enabled": True}
    )
    run(roles, system, table_id)
    city = columns_of(profile_of(roles, system, table_id))["city"]
    assert city["profile"]["min"] == "Alexandria"

    version = next(c["version"] for c in table_of(roles, system)["columns"] if c["name"] == "city")
    roles.client("editor").patch(
        f"{system}/tables/{table_id}/columns/{city['column_id']}",
        json={"version": version, "is_sensitive": True},
    )

    redacted = columns_of(profile_of(roles, system, table_id))["city"]["profile"]
    assert redacted["min"] is None and redacted["max"] is None and redacted["top_values"] is None


def test_the_sample_is_capped(roles: RoleClients, profiled):
    system, table_id = profiled

    run(roles, system, table_id, row_cap=5)

    score = columns_of(profile_of(roles, system, table_id))["score"]["profile"]
    assert score["row_count"] == 5 and score["row_cap"] == 5 and score["sampled"] is True


def test_a_slow_source_query_is_stopped_by_the_timeout(roles: RoleClients, scratch_source):
    scratch_source["run"](
        "CREATE VIEW slow AS SELECT i AS v FROM generate_series(1, 5) i "
        "WHERE pg_sleep(0.7)::text IS NOT NULL;"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)

    job = run(roles, system, table_of(roles, system, "slow")["id"], timeout_seconds=1)

    assert job["status"] == "failed"
    assert "statement timeout" in job["log"]


def test_profiling_is_unavailable_without_a_live_connection(roles: RoleClients):
    system = add_system(roles)
    upload(roles, system, csvs())
    imported = table_of(roles, system, "Customer")

    response = profile_tables(roles, system, [imported["id"]])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "profiling_unavailable"
    assert "Schema Import" in response.json()["error"]["message"]


def test_a_table_of_another_system_or_a_viewer_is_refused(roles: RoleClients, profiled):
    system, table_id = profiled
    assert profile_tables(roles, system, [table_id], as_role="viewer").status_code == 403
    other = add_system_two(roles)
    assert profile_tables(roles, other, [table_id]).status_code in (404, 409)
    assert (
        profile_tables(roles, system, ["00000000-0000-0000-0000-000000000000"]).status_code == 404
    )
    assert profile_tables(roles, system, [table_id], row_cap=0).status_code == 422


def add_system_two(roles: RoleClients) -> str:
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Two", "code": "two"}
    )
    return f"/api/v1/workspaces/{roles.workspace_id}/systems/{response.json()['id']}"


def test_a_column_has_its_own_profile_endpoint(roles: RoleClients, profiled):
    system, table_id = profiled
    run(roles, system, table_id)
    column = columns_of(profile_of(roles, system, table_id))["score"]

    response = roles.client("viewer").get(
        f"{system}/tables/{table_id}/columns/{column['column_id']}/profile"
    )

    assert response.status_code == 200
    assert response.json()["profile"]["distinct_count"] == 15


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["a@b.com", "c.d@e.org"], ["email"]),
        (["+20 100 123 4567", "0100-123-4567"], ["phone"]),
        (["2024-01-31", "2023-12-01 10:00:00"], ["iso_date"]),
        (["https://x.org/a", "http://y.com"], ["url"]),
        (["hello", "world"], []),
        (["a@b.com", "nope", "nah", "no", "x"], []),
        ([], []),
    ],
)
def test_patterns_are_detected_by_share_and_never_return_values(values, expected):
    assert detect_patterns(values) == expected


@pytest.mark.parametrize(
    ("data_type", "kind"),
    [
        ("integer", "ordered"),
        ("numeric(14,2)", "ordered"),
        ("timestamp with time zone", "ordered"),
        ("character varying(40)", "text"),
        ("text", "text"),
        ("jsonb", "other"),
        ("boolean", "other"),
        ("bytea", "other"),
    ],
)
def test_a_data_type_decides_what_can_be_computed(data_type, kind):
    assert kind_of(data_type) == kind
