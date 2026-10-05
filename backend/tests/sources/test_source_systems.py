"""Source Systems and System Codes (spec story 39, §4.3, §7)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.roles import RoleClients


def systems_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/systems"


def add(roles: RoleClients, code: str = "cbs", *, as_role="editor", **fields: str):
    body = {"name": "Core Banking", "code": code, **fields}
    return roles.client(as_role).post(systems_path(roles), json=body)


def created(roles: RoleClients, code: str = "cbs", **fields: str) -> dict:
    response = add(roles, code, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def error_code(response) -> str:
    return response.json()["error"]["code"]


def test_an_editor_adds_a_source_system_with_its_details(roles: RoleClients, clock):
    response = add(
        roles,
        "cbs",
        name="  Core Banking  ",
        description="Accounts and ledgers",
        business_owner="Head of Retail",
        technical_owner="DBA team",
    )

    assert response.status_code == 201
    system = response.json()
    assert system == {
        "id": system["id"],
        "workspace_id": str(roles.workspace_id),
        "name": "Core Banking",
        "code": "cbs",
        "description": "Accounts and ledgers",
        "business_owner": "Head of Retail",
        "technical_owner": "DBA team",
        "version": 1,
        "created_at": clock().isoformat().replace("+00:00", "Z"),
        "updated_at": clock().isoformat().replace("+00:00", "Z"),
    }
    opened = roles.client("viewer").get(f"{systems_path(roles)}/{system['id']}")
    assert opened.status_code == 200
    assert opened.json() == system


def test_description_and_owners_are_optional(roles: RoleClients):
    system = created(roles, "crm")

    assert (system["description"], system["business_owner"], system["technical_owner"]) == (
        "",
        "",
        "",
    )


def test_a_source_system_needs_a_name(roles: RoleClients):
    for body in ({"code": "x"}, {"name": "", "code": "x"}, {"name": "  ", "code": "x"}):
        response = roles.client("owner").post(systems_path(roles), json=body)
        assert response.status_code == 422, body


@pytest.mark.parametrize(
    "code", ["", "  ", "CBS", "Core", "1cbs", "_cbs", "c-b-s", "c b s", "cbs.", "كود", "x" * 25]
)
def test_a_system_code_must_be_identifier_safe(roles: RoleClients, code: str):
    response = add(roles, code)

    assert response.status_code == 422
    assert error_code(response) == "invalid_system_code"
    assert roles.client("owner").get(systems_path(roles)).json()["items"] == []


@pytest.mark.parametrize("code", ["c", "cbs", "core_banking", "sys2", "a" * 24])
def test_identifier_safe_codes_are_accepted(roles: RoleClients, code: str):
    assert created(roles, code)["code"] == code


def test_a_system_code_is_unique_per_workspace(roles: RoleClients):
    created(roles, "cbs")

    again = add(roles, "cbs", name="Another")

    assert again.status_code == 409
    assert error_code(again) == "system_code_taken"
    assert len(roles.client("owner").get(systems_path(roles)).json()["items"]) == 1


def test_the_same_code_may_be_used_in_another_workspace(roles: RoleClients):
    created(roles, "cbs")
    other = roles.client("owner").post("/api/v1/workspaces", json={"name": "Other"}).json()

    response = roles.client("owner").post(
        f"/api/v1/workspaces/{other['id']}/systems", json={"name": "Core", "code": "cbs"}
    )

    assert response.status_code == 201


def test_viewers_browse_but_cannot_create_or_edit(roles: RoleClients):
    system = created(roles, "cbs")

    assert roles.client("viewer").get(systems_path(roles)).status_code == 200
    assert add(roles, "crm", as_role="viewer").status_code == 403
    edit = roles.client("viewer").patch(
        f"{systems_path(roles)}/{system['id']}", json={"version": 1, "name": "x"}
    )
    assert edit.status_code == 403


def test_the_list_is_ordered_by_code_and_paged(roles: RoleClients):
    for code in ("crm", "cbs", "hr", "erp"):
        created(roles, code)

    first = roles.client("viewer").get(systems_path(roles), params={"limit": 3}).json()
    assert [s["code"] for s in first["items"]] == ["cbs", "crm", "erp"]
    assert first["next_cursor"] is not None
    last = (
        roles.client("viewer")
        .get(systems_path(roles), params={"limit": 3, "cursor": first["next_cursor"]})
        .json()
    )
    assert [s["code"] for s in last["items"]] == ["hr"]
    assert last["next_cursor"] is None


def test_a_tampered_cursor_is_refused(roles: RoleClients):
    response = roles.client("owner").get(systems_path(roles), params={"cursor": "nonsense"})

    assert response.status_code == 422
    assert error_code(response) == "invalid_cursor"


def test_the_list_shows_only_this_workspaces_systems(roles: RoleClients):
    created(roles, "cbs")
    other = roles.client("owner").post("/api/v1/workspaces", json={"name": "Other"}).json()
    roles.client("owner").post(
        f"/api/v1/workspaces/{other['id']}/systems", json={"name": "HR", "code": "hr"}
    )

    codes = [s["code"] for s in roles.client("owner").get(systems_path(roles)).json()["items"]]

    assert codes == ["cbs"]


def test_a_system_is_not_found_under_another_workspace_or_when_unknown(roles: RoleClients):
    system = created(roles, "cbs")
    other = roles.client("owner").post("/api/v1/workspaces", json={"name": "Other"}).json()

    elsewhere = roles.client("owner").get(
        f"/api/v1/workspaces/{other['id']}/systems/{system['id']}"
    )
    unknown = roles.client("owner").get(f"{systems_path(roles)}/{uuid.uuid4()}")

    assert (elsewhere.status_code, unknown.status_code) == (404, 404)
    assert error_code(elsewhere) == error_code(unknown) == "not_found"


def test_non_members_cannot_tell_a_system_exists(roles: RoleClients):
    system = created(roles, "cbs")

    for role in ("non_member", "admin"):
        client = roles.client(role)
        assert client.get(systems_path(roles)).status_code == 404
        assert client.get(f"{systems_path(roles)}/{system['id']}").status_code == 404


def test_an_editor_edits_the_details(roles: RoleClients, clock):
    system = created(roles, "cbs")
    clock.advance(timedelta(minutes=5))

    response = roles.client("editor").patch(
        f"{systems_path(roles)}/{system['id']}",
        json={"version": 1, "name": " Core ", "technical_owner": "Platform team"},
    )

    assert response.status_code == 200
    edited = response.json()
    assert (edited["name"], edited["technical_owner"], edited["code"]) == (
        "Core",
        "Platform team",
        "cbs",
    )
    assert edited["version"] == 2
    assert edited["updated_at"] > system["updated_at"]
    assert edited["description"] == system["description"]


def test_a_stale_version_is_a_conflict(roles: RoleClients):
    system = created(roles, "cbs")
    path = f"{systems_path(roles)}/{system['id']}"
    roles.client("owner").patch(path, json={"version": 1, "name": "First"})

    stale = roles.client("editor").patch(path, json={"version": 1, "name": "Second"})

    assert stale.status_code == 409
    assert error_code(stale) == "version_conflict"
    assert stale.json()["error"]["details"] == {"current_version": 2}
    assert roles.client("owner").get(path).json()["name"] == "First"


def test_only_an_owner_changes_the_system_code(roles: RoleClients):
    system = created(roles, "cbs")
    path = f"{systems_path(roles)}/{system['id']}"

    refused = roles.client("editor").patch(path, json={"version": 1, "code": "core"})
    assert refused.status_code == 403
    assert roles.client("owner").get(path).json()["code"] == "cbs"

    changed = roles.client("owner").patch(path, json={"version": 1, "code": "core"})
    assert changed.status_code == 200
    assert changed.json()["code"] == "core"
    assert changed.json()["version"] == 2


def test_an_editor_may_send_the_unchanged_code(roles: RoleClients):
    system = created(roles, "cbs")

    response = roles.client("editor").patch(
        f"{systems_path(roles)}/{system['id']}",
        json={"version": 1, "code": "cbs", "name": "Renamed"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_the_new_system_code_is_validated_and_unique(roles: RoleClients):
    created(roles, "crm")
    system = created(roles, "cbs")
    path = f"{systems_path(roles)}/{system['id']}"

    invalid = roles.client("owner").patch(path, json={"version": 1, "code": "Bad Code"})
    taken = roles.client("owner").patch(path, json={"version": 1, "code": "crm"})

    assert (invalid.status_code, error_code(invalid)) == (422, "invalid_system_code")
    assert (taken.status_code, error_code(taken)) == (409, "system_code_taken")
    unchanged = roles.client("owner").get(path).json()
    assert (unchanged["code"], unchanged["version"]) == ("cbs", 1)


def test_a_refused_edit_changes_nothing(roles: RoleClients, signed_in_client: TestClient):
    system = created(roles, "cbs")

    response = roles.client("editor").patch(
        f"{systems_path(roles)}/{system['id']}", json={"version": 1, "name": ""}
    )

    assert response.status_code == 422
    assert roles.client("owner").get(f"{systems_path(roles)}/{system['id']}").json() == system


def test_a_stale_form_with_an_old_code_is_a_conflict_not_a_refusal(roles: RoleClients):
    system = created(roles, "cbs")
    path = f"{systems_path(roles)}/{system['id']}"
    roles.client("owner").patch(path, json={"version": 1, "code": "core"})

    stale = roles.client("editor").patch(path, json={"version": 1, "code": "cbs", "name": "X"})

    assert stale.status_code == 409
    assert error_code(stale) == "version_conflict"


def test_a_non_member_gets_the_same_answer_for_missing_and_existing_systems(roles: RoleClients):
    system = created(roles, "cbs")
    client = roles.client("non_member")

    existing = client.get(f"{systems_path(roles)}/{system['id']}")
    missing = client.get(f"{systems_path(roles)}/{uuid.uuid4()}")

    assert existing.status_code == missing.status_code == 404
    assert existing.json() == missing.json()


def feed(roles: RoleClients) -> list[tuple[str, str, str, str | None]]:
    """(actor, verb, object label, code) of the Workspace's feed, newest first."""
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/activity")
    assert response.status_code == 200, response.text
    return [
        (i["actor"]["display_name"], i["verb"], i["object_label"], (i["details"] or {}).get("code"))
        for i in response.json()["items"]
        if i["object_type"] == "source_system"
    ]


def test_activity_events_are_recorded(roles: RoleClients, clock):
    system = created(roles, "cbs")
    path = f"{systems_path(roles)}/{system['id']}"
    clock.advance(timedelta(minutes=1))
    roles.client("editor").patch(path, json={"version": 1, "name": "Core"})
    clock.advance(timedelta(minutes=1))
    roles.client("owner").patch(path, json={"version": 2, "code": "core"})

    assert feed(roles) == [
        ("Owner", "source_system.code_changed", "Core", "core"),
        ("Editor", "source_system.edited", "Core", "cbs"),
        ("Editor", "source_system.created", "Core Banking", "cbs"),
    ]


def test_a_refused_change_records_no_activity(roles: RoleClients):
    system = created(roles, "cbs")
    add(roles, "cbs")  # duplicate code
    roles.client("editor").patch(
        f"{systems_path(roles)}/{system['id']}", json={"version": 1, "code": "x2"}
    )

    assert [verb for _, verb, _, _ in feed(roles)] == ["source_system.created"]
