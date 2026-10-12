"""The Core and Mart Layer Approval (spec §6.10, stories 80, 107, 120), through the HTTP API.

Approval exists for Core and Mart only. A structural change to a Core or Mart object (a
direct edit or an accepted propagation item) returns an approved Layer to draft, audited
with its cause; text edits and staging-only changes revoke nothing. Mart is approved only
while Core is, and revoking Core revokes Mart.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from dawam.platform.clock import FakeClock
from tests.propagation.test_propagation import (
    accept,
    column,
    dw,
    edit_column,
    only_propagation,
    table,
)
from tests.propagation.test_propagation import layered as layered  # a fixture
from tests.propagation.test_propagation import model as model  # a fixture
from tests.propagation.test_propagation import warehouse as warehouse  # a fixture
from tests.roles import RoleClients


def approvals(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse/approvals"


def states(roles: RoleClients) -> dict[str, str]:
    response = roles.client("viewer").get(approvals(roles))
    assert response.status_code == 200, response.text
    return {item["layer"]: item["status"] for item in response.json()["items"]}


def clear_errors(roles: RoleClients) -> None:
    """Disable the Core and Mart checks that fail as errors: errors block approval (spec
    §6.11), and these designs are sketches, not clean models."""
    result = roles.client("owner").get(f"{dw(roles)}/score").json()
    codes = {
        f["check_code"]
        for f in result["failed_checks"]
        if f["severity"] == "error" and f["layer"] in ("core", "mart")
    }
    for code in codes:
        response = roles.client("owner").put(
            f"{dw(roles)}/score/disabled-checks/{code}", json={"reason": "A sketch model"}
        )
        assert response.status_code == 200, response.text


def move(roles: RoleClients, layer: str, status: str, role="owner", *, clear=True):
    """Move a Layer; approving first disables the failing error checks unless ``clear`` is off."""
    if status == "approved" and clear:
        clear_errors(roles)
    return roles.client(role).put(f"{approvals(roles)}/{layer}", json={"status": status})


def approve(roles: RoleClients, *layers: str) -> None:
    for layer in layers:
        response = move(roles, layer, "approved")
        assert response.status_code == 200, response.text


def save_mapping(roles: RoleClients, target: dict, **body):
    """Save a column's mapping over its current version."""
    url = f"{dw(roles)}/tables/{target['table_id']}/mapping"
    response = roles.client("viewer").get(url)
    assert response.status_code == 200, response.text
    found = next(c for c in response.json()["columns"] if c["column_id"] == target["id"])
    return roles.client("editor").put(
        f"{url}/columns/{target['id']}", json={"version": found["version"], **body}
    )


def code_of(response) -> str:
    return response.json()["error"]["code"]


def audit(roles: RoleClients) -> list[dict]:
    """The Layer Approval's audit entries, oldest first."""
    response = roles.client("viewer").get(
        f"/api/v1/workspaces/{roles.workspace_id}/audit",
        params={"entity_type": "layer_approval", "limit": 100},
    )
    assert response.status_code == 200, response.text
    return list(reversed(response.json()["items"]))


def revocations(roles: RoleClients) -> dict[str, dict]:
    """The revocation entries, by Layer (one each at most in these tests)."""
    found = [e for e in audit(roles) if "cause" in (e["new"] or {})]
    by_layer = {e["entity_id"]: e for e in found}
    assert len(by_layer) == len(found)
    return by_layer


# -- the lifecycle -------------------------------------------------------------------------


def test_core_and_mart_start_in_draft_and_move_through_review_to_approved(
    warehouse, clock: FakeClock
):
    assert states(warehouse) == {"core": "draft", "mart": "draft"}

    response = move(warehouse, "core", "in_review", role="editor")
    assert response.status_code == 200, response.text
    clock.advance(timedelta(minutes=1))
    assert response.json()["status"] == "in_review"
    response = move(warehouse, "core", "approved", role="editor")
    assert response.status_code == 200, response.text
    approved = response.json()
    assert approved["approved_by"] == str(warehouse.user("editor").id)
    assert approved["approved_at"] is not None

    assert states(warehouse) == {"core": "approved", "mart": "draft"}
    assert [(e["old"], e["new"], e["via"]) for e in audit(warehouse)] == [
        ({"status": "draft"}, {"status": "in_review"}, "user"),
        ({"status": "in_review"}, {"status": "approved"}, "user"),
    ]


def test_staging_has_no_approval_and_viewers_cannot_move_a_layer(warehouse):
    assert move(warehouse, "staging", "approved").status_code == 422
    response = move(warehouse, "core", "approved", role="viewer")
    assert response.status_code == 403
    assert states(warehouse)["core"] == "draft"


def test_mart_can_be_approved_only_while_core_is_approved(warehouse):
    response = move(warehouse, "mart", "approved")
    assert response.status_code == 409
    assert code_of(response) == "core_not_approved"

    approve(warehouse, "core", "mart")
    assert states(warehouse) == {"core": "approved", "mart": "approved"}


def test_moving_core_back_to_review_revokes_mart(warehouse):
    approve(warehouse, "core", "mart")

    assert move(warehouse, "core", "in_review").status_code == 200

    assert states(warehouse) == {"core": "in_review", "mart": "draft"}
    assert list(revocations(warehouse)) == ["mart"]
    revoked = revocations(warehouse)["mart"]
    assert revoked["new"] == {"status": "draft", "cause": {"reason": "core_revoked"}}


# -- what revokes ----------------------------------------------------------------------------


def test_a_structural_edit_of_core_revokes_core_and_mart_with_its_cause(model, warehouse):
    approve(warehouse, "core", "mart")

    response = edit_column(warehouse, model["email"], is_nullable=True)
    assert response.status_code == 200, response.text

    assert states(warehouse) == {"core": "draft", "mart": "draft"}
    found = revocations(warehouse)
    core, mart = found["core"], found["mart"]
    assert (core["old"], core["via"]) == ({"status": "approved"}, "user")
    assert core["actor"]["user_id"] == str(warehouse.user("editor").id)
    assert core["new"]["status"] == "draft"
    assert core["new"]["cause"]["reason"] == "structural_change"
    assert core["new"]["cause"]["changes"] == [
        {"object_type": "dw_column", "object_id": model["email"]["id"], "fields": ["is_nullable"]}
    ]
    assert mart["new"]["cause"] == {"reason": "core_revoked"}


def test_a_text_edit_never_revokes(model, warehouse):
    approve(warehouse, "core", "mart")

    response = edit_column(warehouse, model["email"], description="The customer's email")
    assert response.status_code == 200, response.text
    url = f"{dw(warehouse)}/tables/{model['core']['id']}"
    current = warehouse.client("viewer").get(url).json()
    response = warehouse.client("editor").patch(
        url, json={"version": current["version"], "description": "Customers"}
    )
    assert response.status_code == 200, response.text
    response = save_mapping(
        warehouse,
        model["segment_m"],
        mapping_type="direct",
        sql_expression="dim_customer.segment",
        rule_text="Copied as is",
    )
    assert response.status_code == 200, response.text

    assert states(warehouse) == {"core": "approved", "mart": "approved"}
    assert revocations(warehouse) == {}


def test_a_mapping_change_in_mart_revokes_mart_only(model, warehouse):
    approve(warehouse, "core", "mart")

    response = save_mapping(
        warehouse,
        model["segment_m"],
        mapping_type="derived",
        sql_expression="UPPER(dim_customer.segment)",
    )
    assert response.status_code == 200, response.text

    assert states(warehouse) == {"core": "approved", "mart": "draft"}
    assert list(revocations(warehouse)) == ["mart"]
    mart = revocations(warehouse)["mart"]
    assert {c["object_type"] for c in mart["new"]["cause"]["changes"]} == {"column_mapping"}


def test_a_new_core_table_revokes_core(warehouse):
    approve(warehouse, "core")

    table(warehouse, "dim_product", "core")

    assert states(warehouse)["core"] == "draft"


def test_a_staging_change_revokes_nothing_until_its_propagation_reaches_core(layered, warehouse):
    approve(warehouse, "core", "mart")

    response = edit_column(warehouse, layered["stg_name"], name="customer_name")
    assert response.status_code == 200, response.text
    assert states(warehouse) == {"core": "approved", "mart": "approved"}

    detail = only_propagation(warehouse)
    accept(warehouse, detail["change_set"]["id"])

    assert states(warehouse) == {"core": "draft", "mart": "draft"}
    core = revocations(warehouse)["core"]
    assert core["via"] == "propagation"
    assert core["new"]["cause"]["reason"] == "structural_change"


# -- "approver must not be the last editor" ---------------------------------------------------


def settings(roles: RoleClients, required: bool, role="owner"):
    return roles.client(role).put(
        f"/api/v1/workspaces/{roles.workspace_id}/approval-settings",
        json={"require_distinct_approver": required},
    )


def test_the_last_editor_cannot_approve_when_the_owner_requires_another_approver(warehouse):
    warehouse.client("editor")  # a second editor (the owner is the first)
    response = settings(warehouse, True)
    assert response.status_code == 200, response.text
    assert response.json()["require_distinct_approver"] is True

    core = table(warehouse, "dim_store", "core")
    column(warehouse, core["id"], "store_name")  # the editor is the last editor

    refused = move(warehouse, "core", "approved", role="editor")
    assert refused.status_code == 409
    assert code_of(refused) == "approver_is_last_editor"
    assert move(warehouse, "core", "in_review", role="editor").status_code == 200

    assert move(warehouse, "core", "approved", role="owner").status_code == 200
    [core_state, _] = warehouse.client("viewer").get(approvals(warehouse)).json()["items"]
    assert core_state["last_editor_id"] == str(warehouse.user("editor").id)


def test_the_setting_is_off_by_default_and_the_last_editor_may_approve(warehouse):
    assert (
        warehouse.client("owner")
        .get(f"/api/v1/workspaces/{warehouse.workspace_id}")
        .json()["require_distinct_approver"]
        is False
    )
    table(warehouse, "dim_store", "core")

    assert move(warehouse, "core", "approved", role="editor").status_code == 200


def test_the_setting_cannot_be_turned_on_with_a_single_editor(roles: RoleClients):
    roles.client("viewer")  # a viewer is no editor

    response = settings(roles, True)

    assert response.status_code == 422
    assert code_of(response) == "single_editor"
    assert settings(roles, False).status_code == 200


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_only_owners_change_the_setting(roles: RoleClients, role):
    assert settings(roles, False, role=role).status_code == 403
