"""Disabled checks and the approval gate, end to end (spec §6.11, stories 119, 120): an owner
switches checks off with a reason and sets a minimum score per Layer; a Core or Mart Layer
cannot be approved while it has errors or is below its minimum; Staging has no gate."""

from __future__ import annotations

import pytest

from tests.roles import RoleClients
from tests.warehouse.test_column_mappings import base, column
from tests.warehouse.test_layer_approval import approvals, audit, code_of, states
from tests.warehouse.test_score_api import (
    fact,
    failed,
    layer,
    score,
)
from tests.warehouse.test_score_api import warehouse as warehouse  # a fixture

NO_DIMENSION = "fact_has_dimension_fk"
"""An error on a fact that links to no dimension."""
NO_ADDITIVITY = "measure_has_additivity"
"""A warning on a measure without additivity."""


def score_url(roles: RoleClients) -> str:
    return f"{base(roles)}/score"


def disable(roles: RoleClients, code: str, reason="Accepted for this model", role="owner"):
    return roles.client(role).put(
        f"{score_url(roles)}/disabled-checks/{code}", json={"reason": reason}
    )


def move(roles: RoleClients, layer: str, status: str):
    """Move a Layer as it is: unlike the approval tests', nothing is cleared first."""
    return roles.client("owner").put(f"{approvals(roles)}/{layer}", json={"status": status})


def set_gate(roles: RoleClients, name: str, min_score=0, role="owner", **body):
    return roles.client(role).put(
        f"{score_url(roles)}/gates/{name}", json={"min_score": min_score, **body}
    )


# -- disabled checks -----------------------------------------------------------------------


def test_an_owner_disables_a_check_with_a_reason_and_it_leaves_the_score(warehouse: RoleClients):
    fact(warehouse)
    before = score(warehouse)
    assert failed(before, NO_DIMENSION) and before["disabled_checks"] == []

    response = disable(warehouse, NO_DIMENSION, "Dimensions come in the next release")

    assert response.status_code == 200, response.text
    after = score(warehouse)
    assert failed(after, NO_DIMENSION) == []  # excluded from the formula and the failures
    assert after["score"] > before["score"] and not after["capped"]
    [listed] = after["disabled_checks"]
    assert (listed["check_code"], listed["severity"]) == (NO_DIMENSION, "error")
    assert listed["reason"] == "Dimensions come in the next release"
    assert listed["title"]
    entries = warehouse.client("viewer").get(
        f"/api/v1/workspaces/{warehouse.workspace_id}/audit",
        params={"entity_type": "score_check", "limit": 10},
    )
    assert [e["entity_id"] for e in entries.json()["items"]] == [NO_DIMENSION]


def test_enabling_a_check_again_restores_the_failure(warehouse: RoleClients):
    fact(warehouse)
    disable(warehouse, NO_DIMENSION)

    response = warehouse.client("owner").delete(
        f"{score_url(warehouse)}/disabled-checks/{NO_DIMENSION}"
    )

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    result = score(warehouse)
    assert failed(result, NO_DIMENSION) and result["disabled_checks"] == []
    missing = warehouse.client("owner").delete(
        f"{score_url(warehouse)}/disabled-checks/{NO_DIMENSION}"
    )
    assert missing.status_code == 404 and code_of(missing) == "not_disabled"


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_only_owners_disable_checks(warehouse: RoleClients, role: str):
    assert disable(warehouse, NO_DIMENSION, role=role).status_code == 403
    assert (
        warehouse.client(role)
        .delete(f"{score_url(warehouse)}/disabled-checks/{NO_DIMENSION}")
        .status_code
        == 403
    )
    listing = warehouse.client(role).get(f"{score_url(warehouse)}/disabled-checks")
    assert listing.status_code == 200 and listing.json()["items"] == []


def test_a_disabled_check_needs_a_known_code_and_a_reason(warehouse: RoleClients):
    unknown = disable(warehouse, "no_such_check")
    assert unknown.status_code == 404 and code_of(unknown) == "unknown_check"
    blank = disable(warehouse, NO_DIMENSION, "   ")
    assert blank.status_code == 422 and code_of(blank) == "reason_required"
    assert score(warehouse)["disabled_checks"] == []


# -- gates ---------------------------------------------------------------------------------


def test_only_owners_set_a_minimum_score_on_core_and_mart(warehouse: RoleClients):
    response = set_gate(warehouse, "core", 80)
    assert response.status_code == 200, response.text
    assert response.json() | {"updated_by": None, "updated_at": None} == {
        "layer": "core",
        "min_score": 80,
        "configured": True,
        "updated_by": None,
        "updated_at": None,
    }
    for role in ("editor", "viewer"):
        assert set_gate(warehouse, "core", 10, role=role).status_code == 403
    assert set_gate(warehouse, "staging", 10).status_code == 422
    assert set_gate(warehouse, "core", 101).status_code == 422
    listing = warehouse.client("viewer").get(f"{score_url(warehouse)}/gates").json()["items"]
    assert [(g["layer"], g["min_score"], g["configured"]) for g in listing] == [
        ("core", 80, True),
        ("mart", 0, False),
    ]


# -- the approval gate ---------------------------------------------------------------------


def test_errors_block_approval_until_fixed_or_disabled(warehouse: RoleClients):
    fact(warehouse)  # no minimum is set: errors block all the same

    blocked = move(warehouse, "core", "approved")

    assert blocked.status_code == 409 and code_of(blocked) == "score_gate_failed"
    assert blocked.json()["error"]["details"]["error_count"] >= 1
    assert states(warehouse)["core"] == "draft"
    assert move(warehouse, "core", "in_review").status_code == 200  # only approving is gated

    disable(warehouse, NO_DIMENSION)

    assert move(warehouse, "core", "approved").status_code == 200
    assert states(warehouse)["core"] == "approved"


def test_a_score_below_the_minimum_blocks_approval(warehouse: RoleClients):
    sales = fact(warehouse)
    column(warehouse, sales["id"], "amount", "decimal", role="measure")
    disable(warehouse, NO_DIMENSION)  # leaves only warnings and infos
    result = score(warehouse)
    assert failed(result, NO_ADDITIVITY) and layer(result, "core")["score"] < 100

    set_gate(warehouse, "core", 100)
    blocked = move(warehouse, "core", "approved")
    assert blocked.status_code == 409 and code_of(blocked) == "score_gate_failed"
    assert blocked.json()["error"]["details"]["min_score"] == 100

    set_gate(warehouse, "core", 1)
    assert move(warehouse, "core", "approved").status_code == 200


def test_a_clean_layer_without_a_gate_and_staging_are_not_blocked(warehouse: RoleClients):
    assert move(warehouse, "core", "approved").status_code == 200  # not scored: no errors
    assert move(warehouse, "staging", "approved").status_code == 422  # no approval, no gate
    assert warehouse.client("viewer").get(approvals(warehouse)).status_code == 200


def test_changes_to_gates_and_checks_are_audited(warehouse: RoleClients):
    set_gate(warehouse, "mart", 70)
    entries = warehouse.client("viewer").get(
        f"/api/v1/workspaces/{warehouse.workspace_id}/audit",
        params={"entity_type": "score_gate", "limit": 10},
    )
    [entry] = entries.json()["items"]
    assert (entry["entity_id"], entry["new"]["min_score"]) == ("mart", 70)
    assert audit(warehouse) == []  # the Layer Approval's own trail is untouched


def test_an_unapproved_core_is_named_before_the_gate(warehouse: RoleClients):
    fact(warehouse, layer="mart")  # an error in Mart, and Core is not approved

    blocked = move(warehouse, "mart", "approved")

    assert blocked.status_code == 409 and code_of(blocked) == "core_not_approved"
