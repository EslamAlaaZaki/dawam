"""Custom PII rules (spec §6.12, story 136).

The matching is unit-tested on its own; the CRUD, the audit trail and the effect on name and
value scans are driven through the HTTP API.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.sources.internal.pii_rules import (
    RULES,
    RuleSet,
    compile_custom_rule,
    match_name,
)
from dawam.modules.sources.internal.pii_scan import score_column
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_pii_findings import by_column, findings
from tests.sources.test_pii_value_scan import scan
from tests.sources.test_schema_browser import schema

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""

EMPLOYEE = {
    "name": "employee_number",
    "keywords": ["emp_no", "staff number"],
    "pattern": r"E\d{6}",
    "category": "direct_identifier",
    "confidence": 0.8,
}
NUMBERS = ["E100001", "E100002", "E100003", "E100004"]


def ruleset(**overrides) -> RuleSet:
    body = {**EMPLOYEE, **overrides}
    return RuleSet(
        custom=(
            compile_custom_rule(
                body["name"],
                keywords=body["keywords"],
                pattern=body["pattern"],
                category=body["category"],
                confidence=body["confidence"],
            ),
        )
    )


# -- matching ----------------------------------------------------------------------------


def test_a_keyword_matches_the_normalised_column_name():
    rules = ruleset()

    for name in ("EMP_NO", "emp-no", "Staff Number", "old_staffnumber"):
        found = match_name(name, rules)
        assert found is not None and found.rule == "custom:employee_number", name
        assert (found.category, found.confidence) == ("direct_identifier", 0.8)
        assert name in found.evidence
    assert match_name("employee_name", rules).rule == "person_name"  # type: ignore[union-attr]
    assert match_name("branch", rules) is None


def test_a_custom_rule_wins_over_a_built_in_one_on_the_same_name():
    rules = ruleset(keywords=["phone"])

    assert match_name("phone", rules).rule == "custom:employee_number"  # type: ignore[union-attr]
    assert match_name("phone").rule == "phone"  # type: ignore[union-attr]


def test_a_disabled_built_in_rule_no_longer_matches():
    rules = RuleSet(disabled=frozenset({"email"}))

    assert match_name("email", rules) is None
    assert match_name("phone", rules) is not None


def test_a_custom_rule_without_a_pattern_is_name_only():
    rules = ruleset(pattern=None)

    assert match_name("emp_no", rules) is not None
    assert score_column("ref", NUMBERS, rules=rules) is None


def test_the_pattern_is_tested_against_whole_sampled_values():
    rules = ruleset()

    found = score_column("ref", [*NUMBERS, "xE100005x"], rules=rules)

    assert found is not None
    assert (found.rule, found.category, found.matched, found.total) == (
        "custom:employee_number",
        "direct_identifier",
        4,
        5,
    )
    assert found.confidence == pytest.approx(0.8 * 0.9)
    assert not any(value in found.evidence for value in NUMBERS)
    assert score_column("ref", ["E1", "E2", "E3"], rules=rules) is None
    assert score_column("ref", NUMBERS) is None  # no custom rules: nothing


def test_a_custom_rule_name_match_raises_the_value_confidence():
    rules = ruleset()
    values = [*NUMBERS[:2], "x", "y", "z"]  # 40 % match

    plain = score_column("ref", values, rules=rules)
    named = score_column("emp_no", values, rules=rules)

    assert plain is None  # 0.4 * 0.9 is under the review threshold
    assert named is not None and named.confidence > 0.8
    assert "name also matches" in named.evidence


def test_a_disabled_built_in_validator_is_not_scored():
    ids = ["1000000008", "1234567897", "1087654321", "1000000016"]

    assert score_column("ref", ids) is not None
    assert score_column("ref", ids, rules=RuleSet(disabled=frozenset({"national_id"}))) is None


# -- compiling ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Bad Name"},
        {"name": "x" * 41},
        {"name": "e", "keywords": [], "pattern": None},
        {"name": "e", "pattern": "("},
        {"name": "e", "pattern": "(a+)+$"},
        {"name": "e", "pattern": "a" * 201},
        {"name": "e", "category": "nope"},
        {"name": "e", "confidence": 0.2},
        {"name": "e", "keywords": ["___"], "pattern": None},
    ],
)
def test_an_invalid_rule_is_refused(kwargs):
    body = {**EMPLOYEE, **kwargs}
    with pytest.raises(ValueError):
        compile_custom_rule(
            body["name"],
            keywords=body["keywords"],
            pattern=body["pattern"],
            category=body["category"],
            confidence=body["confidence"],
        )


# -- the API -----------------------------------------------------------------------------


def url(roles: RoleClients, tail: str = "") -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/pii-rules{tail}"


def create(roles: RoleClients, as_role="owner", **overrides):
    return roles.client(as_role).post(url(roles), json={**EMPLOYEE, **overrides})


def test_an_owner_adds_edits_lists_and_deletes_a_custom_rule(roles: RoleClients):
    made = create(roles)

    assert made.status_code == 201, made.text
    rule = made.json()
    assert (rule["name"], rule["category"], rule["pattern"]) == (
        "employee_number",
        "direct_identifier",
        r"E\d{6}",
    )
    owner = roles.client("owner")
    edited = owner.patch(
        url(roles, f"/{rule['id']}"), json={"confidence": 0.95, "keywords": ["x1"]}
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["confidence"], edited.json()["keywords"]) == (0.95, ["x1"])
    listed = owner.get(url(roles)).json()
    assert [r["name"] for r in listed["custom"]] == ["employee_number"]
    assert {b["id"] for b in listed["built_in"]} >= {"national_id", "email", "iban"}
    assert all(b["enabled"] for b in listed["built_in"])
    assert owner.delete(url(roles, f"/{rule['id']}")).status_code == 204
    assert owner.get(url(roles)).json()["custom"] == []
    assert owner.delete(url(roles, f"/{rule['id']}")).status_code == 404


def test_invalid_and_duplicate_rules_are_refused(roles: RoleClients):
    assert create(roles).status_code == 201

    assert create(roles).status_code == 409
    assert create(roles, name="other", pattern="(").status_code == 422
    assert create(roles, name="other", pattern="(a+)+").status_code == 422
    assert create(roles, name="other", keywords=[], pattern=None).status_code == 422
    assert create(roles, name="Other One").status_code == 422
    assert create(roles, name="other", category="nope").status_code == 422
    assert create(roles, name="other", confidence=0.1).status_code == 422


def test_only_owners_manage_rules(roles: RoleClients):
    rule = create(roles).json()

    for role in ("editor", "viewer"):
        client = roles.client(role)
        assert client.get(url(roles)).status_code == 403
        assert create(roles, role, name="mine").status_code == 403
        assert (
            client.patch(url(roles, f"/{rule['id']}"), json={"confidence": 0.9}).status_code == 403
        )
        assert client.delete(url(roles, f"/{rule['id']}")).status_code == 403
        assert (
            client.patch(url(roles, "/built-in/email"), json={"enabled": False}).status_code == 403
        )


def test_built_in_rules_are_disabled_per_workspace_but_not_edited(roles: RoleClients):
    owner = roles.client("owner")

    off = owner.patch(url(roles, "/built-in/email"), json={"enabled": False})

    assert off.status_code == 200, off.text
    assert off.json() == {
        "id": "email",
        "category": "direct_identifier",
        "confidence": RULES["email"].confidence,
        "enabled": False,
    }
    built_in = {b["id"]: b for b in owner.get(url(roles)).json()["built_in"]}
    assert built_in["email"]["enabled"] is False and built_in["phone"]["enabled"] is True
    assert owner.patch(url(roles, "/built-in/email"), json={"enabled": False}).status_code == 200
    on = owner.patch(url(roles, "/built-in/email"), json={"enabled": True})
    assert on.json()["enabled"] is True
    assert owner.patch(url(roles, "/built-in/email"), json={"pattern": "x"}).status_code == 422
    assert owner.patch(url(roles, "/built-in/nope"), json={"enabled": False}).status_code == 404


def test_rule_changes_are_audited(roles: RoleClients, app: FastAPI):
    owner = roles.client("owner")
    rule = create(roles).json()
    owner.patch(url(roles, f"/{rule['id']}"), json={"confidence": 0.95})
    owner.patch(url(roles, "/built-in/email"), json={"enabled": False})
    owner.patch(url(roles, "/built-in/email"), json={"enabled": False})  # no change
    owner.delete(url(roles, f"/{rule['id']}"))

    audit = AuditService(app.state.engine)
    entries = audit.list(roles.workspace_id, entity_type="pii_rule", entity_id=rule["id"])
    assert sorted((e.old is None, e.new is None) for e in entries) == [
        (False, False),
        (False, True),
        (True, False),
    ]
    assert all(e.via == "user" and e.actor_id == roles.user("owner").id for e in entries)
    [edit] = [e for e in entries if e.old and e.new]
    assert (edit.old, edit.new) == ({"confidence": 0.8}, {"confidence": 0.95})
    toggles = audit.list(roles.workspace_id, entity_type="pii_rule", entity_id="email")
    assert [(t.old, t.new) for t in toggles] == [({"enabled": True}, {"enabled": False})]


def test_custom_rules_run_in_name_scans(roles: RoleClients, scratch_source):
    assert create(roles).status_code == 201
    scratch_source["run"](
        "CREATE TABLE staff (id integer PRIMARY KEY, emp_no text, email text, branch text);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())

    extract(roles, system)

    found = by_column(findings(roles, system))
    assert found[("staff", "emp_no")]["rule"] == "custom:employee_number"
    assert found[("staff", "emp_no")]["is_protected"] is True
    assert ("staff", "email") in found and ("staff", "branch") not in found


def test_a_disabled_built_in_rule_is_skipped_by_name_scans(roles: RoleClients, scratch_source):
    roles.client("owner").patch(url(roles, "/built-in/email"), json={"enabled": False})
    scratch_source["run"]("CREATE TABLE staff (id integer PRIMARY KEY, email text, phone text);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())

    extract(roles, system)

    assert set(by_column(findings(roles, system))) == {("staff", "phone")}


def test_custom_rules_run_in_value_scans(roles: RoleClients, scratch_source):
    assert create(roles).status_code == 201
    scratch_source["run"]("CREATE TABLE staff (id integer PRIMARY KEY, ref text, note text);")
    for i, number in enumerate(NUMBERS):
        scratch_source["run"](f"INSERT INTO staff VALUES ({i}, '{number}', 'free text {i}');")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    table = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}["staff"]

    job = scan(roles, system, [table])

    assert job["status"] == "succeeded"
    found = by_column(findings(roles, system))
    assert set(found) == {("staff", "ref")}
    assert found[("staff", "ref")]["rule"] == "custom:employee_number"
    assert "match ratio 100%" in found[("staff", "ref")]["evidence"]
    assert not any(n in found[("staff", "ref")]["evidence"] for n in NUMBERS)
