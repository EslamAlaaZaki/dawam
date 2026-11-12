"""PII findings and the PII folder (spec stories 130, 133, 134, 135, 139; issue #589).

Behaviour is driven through the HTTP API: a new Snapshot is name-scanned automatically,
Editors accept or reject columns (every finding on a column moves together), add PII by
hand and remove it, and one policy says which columns are protected. The audit trail is
read through its service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.sources import is_protected
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract, latest
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""

NO_SUCH = "00000000-0000-0000-0000-000000000000"


def findings(roles: RoleClients, system: str, as_role="editor", **params) -> list[dict]:
    response = roles.client(as_role).get(f"{system}/pii-findings", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def by_column(items: list[dict]) -> dict[tuple[str, str], dict]:
    return {(f["table"], f["column"]): f for f in items}


def folder(roles: RoleClients, system: str, tab="pii", as_role="editor") -> dict:
    response = roles.client(as_role).get(f"{system}/pii-columns", params={"tab": tab})
    assert response.status_code == 200, response.text
    return response.json()


def tab(roles: RoleClients, system: str, name: str, as_role="editor") -> dict[str, dict]:
    return {c["column"]: c for c in folder(roles, system, name, as_role)["items"]}


def decide(roles: RoleClients, system: str, column_id: str, action: str, **body):
    return roles.client("editor").post(f"{system}/pii-columns/{column_id}/{action}", json=body)


def extracted(roles: RoleClients, sample_source: SampleSource) -> str:
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    return system


def people(roles: RoleClients, scratch_source, ddl: str = "") -> str:
    """A Source System over one ``people`` table: ``email`` matches a name rule, ``note``
    matches none."""
    scratch_source["run"]("CREATE TABLE people (id integer PRIMARY KEY, email text, note text);")
    if ddl:
        scratch_source["run"](ddl)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    return system


def columns(roles: RoleClients, system: str, table: str) -> dict[str, dict]:
    [found] = [t for t in schema(roles, system)["tables"] if t["name"] == table]
    return {c["name"]: c for c in found["columns"]}


def add_finding(app: FastAPI, column_id: str, rule: str, category: str, confidence: float):
    """A second rule on a column, as a value scan would record it."""
    with app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into pii_findings (id, src_column_id, rule, category, confidence,"
                " evidence, status, source, detected_at) values (:id, :c, :rule, :category,"
                " :confidence, '9 of 10 sampled values match', 'suggested', 'value', :now)"
            ),
            {
                "id": uuid.uuid4(),
                "c": column_id,
                "rule": rule,
                "category": category,
                "confidence": confidence,
                "now": datetime.now(UTC),
            },
        )


def test_a_new_snapshot_is_scanned_by_column_name_with_no_extra_step(
    roles: RoleClients, sample_source: SampleSource
):
    system = extracted(roles, sample_source)

    found = by_column(findings(roles, system))

    for key in [
        ("customers", "national_id"),
        ("customers", "email"),
        ("customers", "phone"),
        ("customers", "full_name"),
        ("accounts", "iban"),
    ]:
        assert key in found, key
        assert found[key]["status"] == "suggested"
        assert found[key]["confidence"] >= 0.5
        assert found[key]["is_protected"] is True
        assert found[key]["evidence"]
    assert found[("customers", "national_id")]["category"] == "direct_identifier"
    assert found[("accounts", "iban")]["category"] == "financial"
    assert ("customers", "cust_no") not in found
    assert ("customers", "branch_code") not in found


def test_protection_shows_in_the_source_schema_by_the_one_policy(
    roles: RoleClients, sample_source: SampleSource
):
    system = extracted(roles, sample_source)

    customers = columns(roles, system, "customers")

    assert customers["national_id"]["is_protected"] is True
    assert customers["national_id"]["is_sensitive"] is False
    assert customers["cust_no"]["is_protected"] is False


def test_the_folder_lists_one_row_per_column_with_every_rule_and_the_tab_counts(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    add_finding(app, email["id"], "custom:mail", "sensitive", 0.95)

    body = folder(roles, system, "suggested")

    assert body["counts"] == {"pii": 0, "suggested": 1, "accepted": 0, "rejected": 0}
    [row] = body["items"]
    assert (row["column"], row["state"], row["data_type"]) == ("email", "suggested", "text")
    assert [(r["source"], r["rule"]) for r in row["rules"]] == [
        ("value", "custom:mail"),
        ("name", "email"),
    ]
    assert row["category"] == "sensitive"  # the most confident rule's
    assert body["can_profile"] is True


def test_accepting_a_column_accepts_every_finding_with_the_chosen_category_and_is_audited(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    add_finding(app, email["id"], "custom:mail", "sensitive", 0.95)

    response = decide(roles, system, email["id"], "accept", category="direct_identifier")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "accepted" and body["category"] == "direct_identifier"
    assert {r["status"] for r in body["rules"]} == {"accepted"}
    assert (body["decision"], body["decided_by"]) == ("accepted", str(roles.user("editor").id))
    column = columns(roles, system, "people")["email"]
    assert column["is_sensitive"] is True and column["pii_category"] == "direct_identifier"
    assert set(tab(roles, system, "pii")) == set(tab(roles, system, "accepted")) == {"email"}
    audit = AuditService(app.state.engine)
    for rule in body["rules"]:
        [decision] = audit.list(roles.workspace_id, entity_type="pii_finding", entity_id=rule["id"])
        assert (decision.new, decision.via) == ({"status": "accepted"}, "user")
    [flagged] = audit.list(roles.workspace_id, entity_type="source_column", entity_id=email["id"])
    assert flagged.new == {"is_sensitive": True, "pii_category": "direct_identifier"}


def test_rejecting_a_column_removes_protection_only_when_it_is_not_sensitive(
    roles: RoleClients, sample_source: SampleSource, app: FastAPI
):
    system = extracted(roles, sample_source)
    found = by_column(findings(roles, system))
    plain, flagged = found[("customers", "email")], found[("customers", "phone")]
    phone = columns(roles, system, "customers")["phone"]
    [customers] = [t for t in schema(roles, system)["tables"] if t["name"] == "customers"]
    roles.client("editor").patch(
        f"{system}/tables/{customers['id']}/columns/{phone['id']}",
        json={"version": phone["version"], "is_sensitive": True},
    )

    rejected = decide(roles, system, plain["column_id"], "reject")
    flagged_reject = decide(roles, system, flagged["column_id"], "reject")

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["decision"] == "rejected"
    # A column flagged sensitive is PII already, not waiting for review.
    assert flagged_reject.status_code == 409
    assert columns(roles, system, "customers")["email"]["is_protected"] is False
    assert columns(roles, system, "customers")["phone"]["is_protected"] is True
    assert "email" in tab(roles, system, "rejected")
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="pii_finding", entity_id=plain["id"]
    )
    assert entry.new == {"status": "rejected"}


def test_a_rejected_column_is_accepted_after_all_with_a_category(
    roles: RoleClients, scratch_source
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "reject")

    not_rejected = decide(roles, system, email["id"], "accept", category="sensitive")
    response = decide(roles, system, email["id"], "accept-rejected", category="sensitive")

    assert not_rejected.status_code == 409
    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["category"]) == ("accepted", "sensitive")
    assert tab(roles, system, "rejected") == {}


def test_a_column_is_added_as_pii_by_hand_with_no_finding(roles: RoleClients, scratch_source):
    system = people(roles, scratch_source)
    people_columns = columns(roles, system, "people")

    response = roles.client("editor").post(
        f"{system}/pii-columns",
        json={"column_id": people_columns["note"]["id"], "category": "sensitive"},
    )
    already = roles.client("editor").post(
        f"{system}/pii-columns",
        json={"column_id": people_columns["email"]["id"], "category": "sensitive"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["manual"], body["rules"], body["decision"]) == (True, [], "added")
    assert body["decided_by"] == str(roles.user("editor").id)
    assert already.status_code == 409  # suggested: reviewed in the Suggested tab
    assert set(tab(roles, system, "pii")) == {"note"}
    assert tab(roles, system, "accepted") == {}  # hand-only columns are not in Accepted
    assert columns(roles, system, "people")["note"]["pii_category"] == "sensitive"


def test_adding_a_rejected_column_brings_it_back_with_its_findings(
    roles: RoleClients, scratch_source
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "reject")

    response = roles.client("editor").post(
        f"{system}/pii-columns", json={"column_id": email["id"], "category": "sensitive"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "accepted" and body["manual"] is True
    assert [r["status"] for r in body["rules"]] == ["accepted"]


def test_removing_pii_rejects_every_finding_and_clears_the_flag(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = people(roles, scratch_source)
    people_columns = columns(roles, system, "people")
    email, note = people_columns["email"], people_columns["note"]
    decide(roles, system, email["id"], "accept", category="direct_identifier")
    roles.client("editor").post(
        f"{system}/pii-columns", json={"column_id": note["id"], "category": "sensitive"}
    )

    removed = decide(roles, system, email["id"], "remove")
    hand_only = decide(roles, system, note["id"], "remove")

    assert removed.status_code == 200, removed.text
    column = removed.json()["column"]
    assert (column["state"], column["decision"]) == ("rejected", "removed")
    assert removed.json()["profile_job_id"] is None
    assert hand_only.json()["column"] is None  # it simply leaves the folder
    after = columns(roles, system, "people")
    assert after["email"]["is_sensitive"] is False and after["email"]["pii_category"] is None
    assert after["note"]["is_sensitive"] is False
    assert tab(roles, system, "pii") == {}
    assert set(tab(roles, system, "rejected")) == {"email"}
    assert decide(roles, system, email["id"], "remove").status_code == 409


def test_removing_pii_clears_a_flag_set_in_the_source_schema(roles: RoleClients, scratch_source):
    system = people(roles, scratch_source)
    [people_table] = [t for t in schema(roles, system)["tables"] if t["name"] == "people"]
    note = columns(roles, system, "people")["note"]
    roles.client("editor").patch(
        f"{system}/tables/{people_table['id']}/columns/{note['id']}",
        json={"version": note["version"], "is_sensitive": True},
    )
    assert tab(roles, system, "pii")["note"]["manual"] is True

    response = decide(roles, system, note["id"], "remove")

    assert response.status_code == 200, response.text
    assert columns(roles, system, "people")["note"]["is_sensitive"] is False


def test_a_column_that_becomes_pii_loses_its_stored_profile_values(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    with app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "insert into column_profiles (id, src_column_id, row_count, row_cap, null_pct,"
                " distinct_count, min, max, top_values, profiled_at) values (:id, :c, 10, 100,"
                " 0.1, 9, 'a@x', 'z@x', '[{\"value\": \"a@x\", \"count\": 2}]', :now)"
            ),
            {"id": uuid.uuid4(), "c": email["id"], "now": datetime.now(UTC)},
        )

    decide(roles, system, email["id"], "accept", category="direct_identifier")

    with app.state.engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "select row_count, null_pct, distinct_count, min, max, top_values"
                " from column_profiles where src_column_id = :c"
            ),
            {"c": email["id"]},
        ).one()
    assert tuple(row) == (10, 0.1, 9, None, None, None)
    assert tab(roles, system, "pii")["email"]["table_profiled_at"] is not None


def test_a_rejected_column_stays_rejected_across_snapshots(roles: RoleClients, scratch_source):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "reject")

    scratch_source["run"]("ALTER TABLE people ADD COLUMN remark text;")
    extract(roles, system)

    assert set(tab(roles, system, "rejected")) == {"email"}


def test_a_rejected_column_comes_back_when_a_scan_raises_a_rules_confidence(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "reject")
    with app.state.engine.begin() as connection:  # as if rejected at a lower confidence
        connection.execute(
            sa.text(
                "update pii_findings set confidence = 0.3, confidence_at_rejection = 0.3"
                " where src_column_id = :c"
            ),
            {"c": email["id"]},
        )
    scratch_source["run"]("ALTER TABLE people ADD COLUMN remark text;")  # a new Snapshot

    extract(roles, system)

    back = tab(roles, system, "suggested")["email"]
    assert back["rejected_before_by"] == str(roles.user("editor").id)
    assert back["rejected_before_at"] is not None
    assert back["rules"][0]["confidence"] > 0.3


def test_a_rejected_column_comes_back_when_a_new_rule_matches_it(
    roles: RoleClients, scratch_source
):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "reject")
    rule = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/pii-rules",
        json={"name": "mailbox", "keywords": ["email"], "category": "sensitive"},
    )
    assert rule.status_code == 201, rule.text
    scratch_source["run"]("ALTER TABLE people ADD COLUMN remark text;")  # a new Snapshot

    extract(roles, system)

    back = tab(roles, system, "suggested")["email"]
    assert {r["status"] for r in back["rules"]} == {"suggested"}
    assert len(back["rules"]) == 2


def test_viewers_read_only_the_pii_tab(roles: RoleClients, scratch_source):
    system = people(roles, scratch_source)
    email = columns(roles, system, "people")["email"]
    decide(roles, system, email["id"], "accept", category="direct_identifier")

    pii = roles.client("viewer").get(f"{system}/pii-columns")
    suggested = roles.client("viewer").get(f"{system}/pii-columns", params={"tab": "suggested"})
    accept = roles.client("viewer").post(f"{system}/pii-columns/{email['id']}/remove", json={})

    assert pii.status_code == 200, pii.text
    assert [c["column"] for c in pii.json()["items"]] == ["email"]
    assert suggested.status_code == 403
    assert accept.status_code == 403


def test_only_owners_and_editors_review_and_unknown_columns_are_not_found(
    roles: RoleClients, sample_source: SampleSource
):
    system = extracted(roles, sample_source)
    finding = findings(roles, system)[0]

    viewer_list = roles.client("viewer").get(f"{system}/pii-findings")
    viewer_accept = roles.client("viewer").post(
        f"{system}/pii-columns/{finding['column_id']}/accept",
        json={"category": "sensitive"},
    )
    missing = decide(roles, system, NO_SUCH, "reject")
    bad_status = roles.client("editor").get(f"{system}/pii-findings", params={"status": "nope"})
    bad_category = decide(roles, system, finding["column_id"], "accept", category="nope")

    assert viewer_list.status_code == 403
    assert viewer_accept.status_code == 403
    assert missing.status_code == 404
    assert bad_status.status_code == 422
    assert bad_category.status_code == 422
    assert roles.client("owner").get(f"{system}/pii-findings").status_code == 200


def test_new_suspected_pii_columns_are_highlighted_in_the_snapshot_diff(
    roles: RoleClients, scratch_source
):
    scratch_source["run"]("CREATE TABLE people (id integer PRIMARY KEY, notes text);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    first = latest(roles, system)
    scratch_source["run"](
        "ALTER TABLE people ADD COLUMN iqama_no text, ADD COLUMN remark text;"
        "CREATE TABLE staff (id integer PRIMARY KEY, birth_date date);"
    )
    extract(roles, system)
    second = latest(roles, system)

    response = roles.client("viewer").get(f"{system}/snapshots/{second['id']}/diff/{first['id']}")

    assert response.status_code == 200, response.text
    suspected = {(p["table"], p["column"]): p for p in response.json()["suspected_pii"]}
    assert set(suspected) == {("people", "iqama_no"), ("staff", "birth_date")}
    assert suspected[("people", "iqama_no")]["category"] == "direct_identifier"
    assert suspected[("staff", "birth_date")]["category"] == "quasi_identifier"
    unchanged = roles.client("viewer").get(f"{system}/snapshots/{second['id']}/diff/{second['id']}")
    assert unchanged.json()["suspected_pii"] == []


def test_the_protected_column_policy_is_one_rule():
    assert is_protected(is_sensitive=True, finding_statuses=[])
    assert is_protected(is_sensitive=False, finding_statuses=["suggested"])
    assert is_protected(is_sensitive=False, finding_statuses=["rejected", "accepted"])
    assert not is_protected(is_sensitive=False, finding_statuses=["rejected"])
    assert not is_protected(is_sensitive=False, finding_statuses=[])
    assert is_protected(is_sensitive=True, finding_statuses=["rejected"])
