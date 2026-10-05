"""The KPI catalog (spec §6.13, stories 73, 74, 80, §4.3)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import FastAPI

from dawam.modules.audit import AuditService
from tests.roles import RoleClients


def kpis_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/kpis"


def add(roles: RoleClients, *, as_role="editor", **fields):
    body = {"name": "Net interest margin", **fields}
    return roles.client(as_role).post(kpis_path(roles), json=body)


def created(roles: RoleClients, **fields) -> dict:
    response = add(roles, **fields)
    assert response.status_code == 201, response.text
    return response.json()


def a_system(roles: RoleClients, code: str = "cbs") -> str:
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Core", "code": code}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def error_code(response) -> str:
    return response.json()["error"]["code"]


def test_an_editor_documents_a_kpi_under_a_source_system(roles: RoleClients, clock):
    system_id = a_system(roles)

    response = add(
        roles,
        source_system_id=system_id,
        name="  Net interest margin ",
        definition="Interest earned minus interest paid, over earning assets",
        formula_text="(interest income - interest expense) / average earning assets",
        formula_sql="SELECT 1",
        unit="%",
        aggregation="ratio",
        owner="CFO office",
        refresh_frequency="monthly",
        targets=[{"label": "FY2027", "value": "3.2%"}],
    )

    assert response.status_code == 201
    kpi = response.json()
    assert kpi == {
        "id": kpi["id"],
        "workspace_id": str(roles.workspace_id),
        "source_system_id": system_id,
        "name": "Net interest margin",
        "definition": "Interest earned minus interest paid, over earning assets",
        "formula_text": "(interest income - interest expense) / average earning assets",
        "formula_sql": "SELECT 1",
        "unit": "%",
        "aggregation": "ratio",
        "owner": "CFO office",
        "refresh_frequency": "monthly",
        "targets": [{"label": "FY2027", "value": "3.2%"}],
        "origin": "user",
        "status": "draft",
        "version": 1,
        "created_at": clock().isoformat().replace("+00:00", "Z"),
        "updated_at": clock().isoformat().replace("+00:00", "Z"),
    }
    opened = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}")
    assert opened.status_code == 200
    assert opened.json() == kpi


def test_a_data_warehouse_kpi_needs_no_warehouse_setup(roles: RoleClients):
    kpi = created(roles, name="Customer count", formula_text="count of active customers")

    assert kpi["source_system_id"] is None
    assert kpi["formula_sql"] is None
    warehouse = roles.client("viewer").get(
        f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"
    )
    assert warehouse.json()["set_up"] is False


def test_the_list_can_be_narrowed_to_a_system_or_the_data_warehouse(roles: RoleClients):
    cbs, crm = a_system(roles, "cbs"), a_system(roles, "crm")
    created(roles, name="B", source_system_id=cbs)
    created(roles, name="A", source_system_id=crm)
    created(roles, name="C")
    viewer = roles.client("viewer")

    def names(**params) -> list[str]:
        response = viewer.get(kpis_path(roles), params=params)
        assert response.status_code == 200, response.text
        return [k["name"] for k in response.json()["items"]]

    assert names() == ["A", "B", "C"]
    assert names(source_system_id=cbs) == ["B"]
    assert names(data_warehouse="true") == ["C"]
    both = viewer.get(kpis_path(roles), params={"source_system_id": cbs, "data_warehouse": "true"})
    assert both.status_code == 422
    assert error_code(both) == "invalid_filter"


def test_the_list_is_paginated_by_name(roles: RoleClients):
    for name in ("d", "a", "c", "b"):
        created(roles, name=name)
    viewer = roles.client("viewer")

    first = viewer.get(kpis_path(roles), params={"limit": 3}).json()
    second = viewer.get(
        kpis_path(roles), params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()

    assert [k["name"] for k in first["items"]] == ["a", "b", "c"]
    assert [k["name"] for k in second["items"]] == ["d"]
    assert second["next_cursor"] is None
    bad = viewer.get(kpis_path(roles), params={"cursor": "nope"})
    assert error_code(bad) == "invalid_cursor"


def test_a_kpi_cannot_go_under_a_system_that_is_not_in_the_workspace(roles: RoleClients):
    response = add(roles, source_system_id=str(uuid.uuid4()))

    assert response.status_code == 404


def test_viewers_cannot_write_and_non_members_see_nothing(roles: RoleClients):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"

    assert add(roles, as_role="viewer").status_code == 403
    assert roles.client("viewer").patch(path, json={"version": 1, "unit": "x"}).status_code == 403
    assert roles.client("viewer").delete(path).status_code == 403
    assert roles.client("non_member").get(path).status_code == 404
    assert roles.client("non_member").get(kpis_path(roles)).status_code == 404


def test_editing_changes_only_the_fields_sent_and_bumps_the_version(roles: RoleClients, clock):
    kpi = created(roles, unit="%", owner="CFO")
    path = f"{kpis_path(roles)}/{kpi['id']}"
    clock.advance(timedelta(minutes=5))

    response = roles.client("owner").patch(
        path, json={"version": 1, "definition": "New definition", "formula_sql": "SELECT 2"}
    )

    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["definition"] == "New definition"
    assert edited["formula_sql"] == "SELECT 2"
    assert edited["unit"] == "%"
    assert edited["owner"] == "CFO"
    assert edited["version"] == 2
    assert edited["updated_at"] > kpi["updated_at"]
    cleared = roles.client("owner").patch(path, json={"version": 2, "formula_sql": None})
    assert cleared.json()["formula_sql"] is None
    assert cleared.json()["definition"] == "New definition"


def test_status_moves_between_draft_in_review_and_approved(roles: RoleClients):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"

    review = roles.client("editor").patch(path, json={"version": 1, "status": "in_review"})
    approved = roles.client("owner").patch(path, json={"version": 2, "status": "approved"})
    bad = roles.client("owner").patch(path, json={"version": 3, "status": "done"})

    assert review.json()["status"] == "in_review"
    assert approved.json()["status"] == "approved"
    assert bad.status_code == 422


def test_a_stale_version_is_a_conflict(roles: RoleClients):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"
    roles.client("editor").patch(path, json={"version": 1, "unit": "%"})

    stale = roles.client("owner").patch(path, json={"version": 1, "unit": "x"})

    assert stale.status_code == 409
    assert error_code(stale) == "version_conflict"


def test_invalid_input_is_refused(roles: RoleClients):
    assert add(roles, name="   ").status_code == 422
    too_long = add(roles, definition="x" * 4001)
    assert too_long.status_code == 422
    assert error_code(too_long) == "invalid_kpi"
    assert add(roles, targets=[{"label": "a", "value": "b"}] * 21).status_code == 422
    assert add(roles, targets=[{"label": " ", "value": "b"}]).status_code == 422


def test_a_kpi_is_deleted(roles: RoleClients):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"

    assert roles.client("editor").delete(path).status_code == 204
    assert roles.client("editor").get(path).status_code == 404
    assert roles.client("editor").delete(path).status_code == 404


def test_a_kpi_is_not_reachable_through_another_workspace(roles: RoleClients):
    kpi = created(roles)
    other = roles.client("non_member").post("/api/v1/workspaces", json={"name": "Other"}).json()

    response = roles.client("non_member").get(f"/api/v1/workspaces/{other['id']}/kpis/{kpi['id']}")

    assert response.status_code == 404


def test_changes_are_audited_with_old_and_new_values(roles: RoleClients, app: FastAPI, clock):
    kpi = created(roles, unit="%")
    path = f"{kpis_path(roles)}/{kpi['id']}"
    clock.advance(timedelta(minutes=1))
    roles.client("editor").patch(path, json={"version": 1, "unit": "bps", "status": "in_review"})
    clock.advance(timedelta(minutes=1))
    roles.client("editor").delete(path)

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
    )

    created_entry, edited, deleted = entries
    assert created_entry.old is None
    assert created_entry.new is not None and created_entry.new["unit"] == "%"
    assert created_entry.actor_id == roles.user("editor").id
    assert created_entry.via == "user"
    assert edited.old == {"unit": "%", "status": "draft"}
    assert edited.new == {"unit": "bps", "status": "in_review"}
    assert deleted.old is not None and deleted.old["unit"] == "bps"
    assert deleted.new is None


def feed(roles: RoleClients) -> list[tuple[str, str, str]]:
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/activity")
    assert response.status_code == 200, response.text
    return [
        (i["actor"]["display_name"], i["verb"], i["object_label"])
        for i in response.json()["items"]
        if i["object_type"] == "kpi"
    ]


def test_activity_is_recorded(roles: RoleClients, clock):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"
    clock.advance(timedelta(minutes=1))
    roles.client("editor").patch(path, json={"version": 1, "unit": "%"})
    clock.advance(timedelta(minutes=1))
    roles.client("owner").patch(path, json={"version": 2, "status": "approved"})
    clock.advance(timedelta(minutes=1))
    roles.client("owner").delete(path)

    assert feed(roles) == [
        ("Owner", "kpi.deleted", "Net interest margin"),
        ("Owner", "kpi.status_changed", "Net interest margin"),
        ("Editor", "kpi.edited", "Net interest margin"),
        ("Editor", "kpi.created", "Net interest margin"),
    ]


def test_a_refused_or_empty_change_leaves_no_audit_or_activity(roles: RoleClients, app: FastAPI):
    kpi = created(roles)
    path = f"{kpis_path(roles)}/{kpi['id']}"
    roles.client("editor").patch(path, json={"version": 9, "unit": "%"})
    roles.client("editor").patch(path, json={"version": 1, "unit": ""})  # no change

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
    )
    assert len(entries) == 1
    assert [verb for _, verb, _ in feed(roles)] == ["kpi.created"]
