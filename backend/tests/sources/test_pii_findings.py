"""PII findings and the review queue (spec stories 130, 133, 134, 135, 139).

Behaviour is driven through the HTTP API: a new Snapshot is name-scanned automatically,
Editors confirm or dismiss findings, and one policy says which columns are protected.
The audit trail is read through its service.
"""

from __future__ import annotations

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


def extracted(roles: RoleClients, sample_source: SampleSource) -> str:
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    return system


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

    [customers] = [t for t in schema(roles, system)["tables"] if t["name"] == "customers"]
    columns = {c["name"]: c for c in customers["columns"]}

    assert columns["national_id"]["is_protected"] is True
    assert columns["national_id"]["is_sensitive"] is False
    assert columns["cust_no"]["is_protected"] is False


def test_confirming_flags_the_column_sensitive_with_the_category_and_is_audited(
    roles: RoleClients, sample_source: SampleSource, app: FastAPI
):
    system = extracted(roles, sample_source)
    finding = by_column(findings(roles, system))[("customers", "national_id")]

    response = roles.client("editor").post(f"{system}/pii-findings/{finding['id']}/confirm")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed" and body["is_sensitive"] and body["is_protected"]
    assert body["decided_by"] == str(roles.user("editor").id)
    [customers] = [t for t in schema(roles, system)["tables"] if t["name"] == "customers"]
    column = {c["name"]: c for c in customers["columns"]}["national_id"]
    assert column["is_sensitive"] is True and column["pii_category"] == "direct_identifier"
    audit = AuditService(app.state.engine)
    [decision] = audit.list(roles.workspace_id, entity_type="pii_finding", entity_id=finding["id"])
    assert decision.via == "user" and decision.actor_id == roles.user("editor").id
    assert (decision.old, decision.new) == ({"status": "suggested"}, {"status": "confirmed"})
    [flagged] = audit.list(
        roles.workspace_id, entity_type="source_column", entity_id=finding["column_id"]
    )
    assert flagged.new == {"is_sensitive": True, "pii_category": "direct_identifier"}


def test_dismissing_removes_protection_only_when_the_column_is_not_sensitive(
    roles: RoleClients, sample_source: SampleSource, app: FastAPI
):
    system = extracted(roles, sample_source)
    found = by_column(findings(roles, system))
    plain, flagged = found[("customers", "email")], found[("customers", "phone")]
    [customers] = [t for t in schema(roles, system)["tables"] if t["name"] == "customers"]
    phone = {c["name"]: c for c in customers["columns"]}["phone"]
    roles.client("editor").patch(
        f"{system}/tables/{customers['id']}/columns/{phone['id']}",
        json={"version": phone["version"], "is_sensitive": True},
    )

    dismissed_plain = roles.client("editor").post(f"{system}/pii-findings/{plain['id']}/dismiss")
    dismissed_flagged = roles.client("editor").post(
        f"{system}/pii-findings/{flagged['id']}/dismiss"
    )

    assert dismissed_plain.status_code == 200, dismissed_plain.text
    assert dismissed_plain.json()["status"] == "dismissed"
    assert dismissed_plain.json()["is_protected"] is False
    assert dismissed_flagged.json()["status"] == "dismissed"
    assert dismissed_flagged.json()["is_protected"] is True
    queue = by_column(findings(roles, system, status="suggested"))
    assert ("customers", "email") not in queue and ("customers", "phone") not in queue
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="pii_finding", entity_id=plain["id"]
    )
    assert entry.new == {"status": "dismissed"}


def test_a_decision_survives_the_next_snapshot(roles: RoleClients, scratch_source):
    scratch_source["run"]("CREATE TABLE people (id integer PRIMARY KEY, email text);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    [finding] = findings(roles, system)
    roles.client("editor").post(f"{system}/pii-findings/{finding['id']}/dismiss")

    scratch_source["run"]("ALTER TABLE people ADD COLUMN note text;")
    extract(roles, system)

    [again] = findings(roles, system)
    assert (again["id"], again["status"]) == (finding["id"], "dismissed")


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


def test_only_owners_and_editors_review_and_unknown_findings_are_not_found(
    roles: RoleClients, sample_source: SampleSource
):
    system = extracted(roles, sample_source)
    finding = findings(roles, system)[0]

    viewer_list = roles.client("viewer").get(f"{system}/pii-findings")
    viewer_confirm = roles.client("viewer").post(f"{system}/pii-findings/{finding['id']}/confirm")
    missing = roles.client("editor").post(f"{system}/pii-findings/{NO_SUCH}/dismiss")
    bad_status = roles.client("editor").get(f"{system}/pii-findings", params={"status": "nope"})

    assert viewer_list.status_code == 403
    assert viewer_confirm.status_code == 403
    assert missing.status_code == 404
    assert bad_status.status_code == 422
    assert roles.client("owner").get(f"{system}/pii-findings").status_code == 200


def test_the_protected_column_policy_is_one_rule():
    assert is_protected(is_sensitive=True, finding_statuses=[])
    assert is_protected(is_sensitive=False, finding_statuses=["suggested"])
    assert is_protected(is_sensitive=False, finding_statuses=["dismissed", "confirmed"])
    assert not is_protected(is_sensitive=False, finding_statuses=["dismissed"])
    assert not is_protected(is_sensitive=False, finding_statuses=[])
    assert is_protected(is_sensitive=True, finding_statuses=["dismissed"])
