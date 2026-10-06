"""Source enhancements (spec stories 61, 62): an editor adds descriptions, tags and a
sensitivity flag to tables and columns, and classifies tables with an SCD hint.

Behaviour is driven through the HTTP API; the audit trail is read through its service.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from dawam.modules.audit import AuditService
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_schema_browser import schema

NO_SUCH = "00000000-0000-0000-0000-000000000000"


def extracted(roles: RoleClients, sample_source: SampleSource) -> tuple[str, dict, dict]:
    """A Source System with a Snapshot; its ``core.customers`` table and a column of it."""
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    [table] = [
        t
        for t in schema(roles, system)["tables"]
        if (t["db_schema"], t["name"]) == ("core", "customers")
    ]
    return system, table, table["columns"][0]


def test_an_object_starts_without_enhancements(roles: RoleClients, sample_source: SampleSource):
    _, table, column = extracted(roles, sample_source)

    assert (table["description"], table["tags"], table["is_sensitive"]) == (None, [], False)
    assert (table["classification"], table["scd_hint"]) == (None, None)
    assert (column["description"], column["tags"], column["is_sensitive"]) == (None, [], False)


def test_an_editor_enhances_a_table(roles: RoleClients, sample_source: SampleSource):
    system, table, _ = extracted(roles, sample_source)

    response = roles.client("editor").patch(
        f"{system}/tables/{table['id']}",
        json={
            "version": table["version"],
            "description": "  Bank customers  ",
            "tags": ["master-data", "PII", "PII"],
            "is_sensitive": True,
            "classification": "master",
            "scd_hint": "changes slowly, history matters",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "Bank customers"
    assert body["tags"] == ["master-data", "PII"]
    assert body["is_sensitive"] and body["classification"] == "master"
    assert body["scd_hint"] == "changes slowly, history matters"
    assert body["version"] == table["version"] + 1
    [seen] = [t for t in schema(roles, system)["tables"] if t["id"] == table["id"]]
    assert seen["description"] == "Bank customers" and seen["version"] == body["version"]


def test_an_editor_enhances_a_column_and_clears_a_field(
    roles: RoleClients, sample_source: SampleSource
):
    system, table, column = extracted(roles, sample_source)
    path = f"{system}/tables/{table['id']}/columns/{column['id']}"
    editor = roles.client("editor")

    first = editor.patch(
        path, json={"version": column["version"], "description": "Key", "is_sensitive": True}
    )
    cleared = editor.patch(path, json={"version": first.json()["version"], "description": None})

    assert first.status_code == 200, first.text
    assert first.json()["is_sensitive"] is True
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["description"] is None and cleared.json()["is_sensitive"] is True


def test_a_stale_version_is_refused(roles: RoleClients, sample_source: SampleSource):
    system, table, _ = extracted(roles, sample_source)
    path = f"{system}/tables/{table['id']}"
    roles.client("editor").patch(path, json={"version": table["version"], "tags": ["a"]})

    response = roles.client("owner").patch(path, json={"version": table["version"], "tags": ["b"]})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_invalid_values_are_refused(roles: RoleClients, sample_source: SampleSource):
    system, table, _ = extracted(roles, sample_source)
    editor = roles.client("editor")
    path = f"{system}/tables/{table['id']}"
    v = table["version"]

    bad_class = editor.patch(path, json={"version": v, "classification": "nonsense"})
    empty_tag = editor.patch(path, json={"version": v, "tags": ["  "]})
    long_tag = editor.patch(path, json={"version": v, "tags": ["x" * 41]})

    assert bad_class.status_code == 422
    assert empty_tag.status_code == 422
    assert empty_tag.json()["error"]["code"] == "invalid_enhancement"
    assert long_tag.status_code == 422


def test_a_viewer_cannot_enhance_and_unknown_objects_are_not_found(
    roles: RoleClients, sample_source: SampleSource
):
    system, table, column = extracted(roles, sample_source)
    body = {"version": 1, "tags": ["a"]}

    viewer = roles.client("viewer").patch(f"{system}/tables/{table['id']}", json=body)
    missing = roles.client("editor").patch(f"{system}/tables/{NO_SUCH}", json=body)
    wrong_table = roles.client("editor").patch(
        f"{system}/tables/{NO_SUCH}/columns/{column['id']}", json=body
    )

    assert viewer.status_code == 403
    assert missing.status_code == 404
    assert wrong_table.status_code == 404


def test_changes_are_audited_and_leave_activity(
    roles: RoleClients, sample_source: SampleSource, app: FastAPI, clock
):
    system, table, column = extracted(roles, sample_source)
    clock.advance(timedelta(minutes=1))
    roles.client("editor").patch(
        f"{system}/tables/{table['id']}",
        json={"version": table["version"], "description": "Customers", "classification": "master"},
    )
    clock.advance(timedelta(minutes=1))
    roles.client("editor").patch(
        f"{system}/tables/{table['id']}/columns/{column['id']}",
        json={"version": column["version"], "is_sensitive": True},
    )

    audit = AuditService(app.state.engine)
    [table_entry] = audit.list(
        roles.workspace_id, entity_type="source_table", entity_id=table["id"]
    )
    [column_entry] = audit.list(
        roles.workspace_id, entity_type="source_column", entity_id=column["id"]
    )

    assert table_entry.via == "user" and table_entry.actor_id == roles.user("editor").id
    assert table_entry.old == {"description": None, "classification": None}
    assert table_entry.new == {"description": "Customers", "classification": "master"}
    assert column_entry.old == {"is_sensitive": False}
    assert column_entry.new == {"is_sensitive": True}
    feed = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/activity").json()
    verbs = [i["verb"] for i in feed["items"] if i["verb"].endswith(".enhanced")]
    assert verbs == ["source_column.enhanced", "source_table.enhanced"]


def test_a_refused_or_empty_change_leaves_no_audit(
    roles: RoleClients, sample_source: SampleSource, app: FastAPI
):
    system, table, _ = extracted(roles, sample_source)
    path = f"{system}/tables/{table['id']}"
    roles.client("editor").patch(path, json={"version": 99, "tags": ["a"]})
    unchanged = roles.client("editor").patch(path, json={"version": table["version"], "tags": []})

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="source_table", entity_id=table["id"]
    )
    assert entries == []
    assert unchanged.json()["version"] == table["version"]
