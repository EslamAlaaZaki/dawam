"""AI explains and fixes failed checks (spec §6.11, story 123): ``explain_check`` is a read tool
for every member, ``propose_check_fix`` makes a Change Set and is for editors."""

from __future__ import annotations

from tests.assistant import test_chat
from tests.assistant.test_evaluate_dw import dw, finding_with_change, warehouse
from tests.assistant.test_source_tools import payload, use
from tests.roles import RoleClients

model = test_chat.model  # the assistant's model (a fixture)
__all__ = ["model", "warehouse"]


def a_failed_check(roles: RoleClients) -> dict:
    response = roles.client("viewer").get(f"{dw(roles)}/score")
    assert response.status_code == 200, response.text
    return response.json()["failed_checks"][0]


def test_a_viewer_has_a_failed_check_explained(roles: RoleClients, model, warehouse, fake_llm):
    check = a_failed_check(roles)

    status, seen = use(
        roles,
        fake_llm,
        "explain_check",
        check_code=check["check_code"],
        object_id=check["object_id"],
    )

    assert status == "ok"
    explained = payload(seen)
    assert explained["check"] == check["check_code"]
    assert explained["problem"] == check["message"] and explained["fix_hint"] == check["fix_hint"]


def test_a_check_that_does_not_fail_is_reported(roles: RoleClients, model, warehouse, fake_llm):
    check = a_failed_check(roles)

    status, _ = use(
        roles, fake_llm, "explain_check", check_code="no_such_check", object_id=check["object_id"]
    )

    assert status == "error"


def test_an_editor_proposes_a_fix_as_a_change_set(roles: RoleClients, model, warehouse, fake_llm):
    check = a_failed_check(roles)
    change = finding_with_change(roles, warehouse)["changes"][0]

    status, seen = use(
        roles,
        fake_llm,
        "propose_check_fix",
        as_role="editor",
        check_code=check["check_code"],
        object_id=check["object_id"],
        title="Fix the check",
        changes=[change],
    )

    assert status == "ok"
    proposed = payload(seen)
    assert proposed["status"] == "proposed" and proposed["items"] == 1
    detail = roles.client("editor").get(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{proposed['change_set_id']}"
    )
    assert detail.json()["change_set"]["origin"] == "ai"


def test_a_viewer_cannot_propose_a_fix(roles: RoleClients, model, warehouse, fake_llm):
    check = a_failed_check(roles)
    change = finding_with_change(roles, warehouse)["changes"][0]

    status, _ = use(
        roles,
        fake_llm,
        "propose_check_fix",
        as_role="viewer",
        check_code=check["check_code"],
        object_id=check["object_id"],
        title="Fix the check",
        changes=[change],
    )

    assert status == "refused"
