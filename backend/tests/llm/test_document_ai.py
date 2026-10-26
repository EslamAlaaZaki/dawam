"""Document AI settings (24.1): the RAG pipeline's knobs, installation defaults and bounds
set by admins, per-Workspace overrides set by owners within those bounds. Every change is
validated, audited and versioned. Driven through the HTTP API with the signed-in clients.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.auth import SecurityEventRecorder
from tests.roles import RoleClients

ADMIN = "/api/v1/admin/llm/document-ai"


def workspace_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/document-ai"


def set_installation(roles: RoleClients, *, values=None, bounds=None, version=None):
    admin = roles.client("admin")
    if version is None:
        version = admin.get(ADMIN).json()["version"]
    return admin.put(
        ADMIN, json={"version": version, "values": values or {}, "bounds": bounds or {}}
    )


def set_overrides(roles: RoleClients, overrides: dict, *, as_role="owner", version=None):
    client = roles.client(as_role)
    if version is None:
        version = roles.client("owner").get(workspace_path(roles)).json()["version"]
    return client.put(workspace_path(roles), json={"version": version, "overrides": overrides})


def code(response) -> str:
    return response.json()["error"]["code"]


# -- installation settings (admins) --------------------------------------------------


def test_admins_see_every_knob_with_its_default_and_who_may_override_it(roles: RoleClients):
    response = roles.client("admin").get(ADMIN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 0
    assert body["values"]["chunk_size"] == 250
    assert body["values"]["chunking_strategy"] == "headings"
    assert body["values"]["rrf_k"] == 60
    knobs = {k["key"]: k for k in body["knobs"]}
    assert knobs["top_k"]["owner"] is True
    assert knobs["embed_batch"]["owner"] is False
    assert knobs["chunk_size"]["reindex"] is True
    assert body["bounds"]["top_k"] == {"min": 1, "max": 50}
    assert "rrf_k" not in body["bounds"]  # admin-only: no bounds for owners


def test_only_admins_read_or_change_the_installation_settings(roles: RoleClients):
    for role in ("owner", "viewer"):
        assert roles.client(role).get(ADMIN).status_code == 403
        assert roles.client(role).put(ADMIN, json={"version": 0, "values": {}}).status_code == 403


def test_an_admin_change_is_versioned_and_a_stale_version_is_refused(roles: RoleClients):
    first = set_installation(roles, values={"rrf_k": 80}, version=0)
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    assert first.json()["values"]["rrf_k"] == 80

    stale = set_installation(roles, values={"rrf_k": 90}, version=0)
    assert stale.status_code == 409
    assert code(stale) == "settings_changed"
    assert roles.client("admin").get(ADMIN).json()["values"]["rrf_k"] == 80


def test_values_are_validated(roles: RoleClients):
    cases = [
        ({"values": {"chunk_size": 5}}, "out_of_range", "chunk_size"),
        ({"values": {"top_k": "many"}}, "invalid_setting", "top_k"),
        ({"values": {"chunking_strategy": "bogus"}}, "invalid_setting", "chunking_strategy"),
        ({"values": {"nope": 1}}, "unknown_setting", "nope"),
        ({"values": {"chunk_size": 90, "chunk_overlap": 90}}, "invalid_setting", "chunk_overlap"),
        ({"bounds": {"top_k": {"min": 20, "max": 5}}}, "invalid_bounds", "top_k"),
        ({"bounds": {"rrf_k": {"min": 1, "max": 5}}}, "not_overridable", "rrf_k"),
        (
            {"values": {"top_k": 30}, "bounds": {"top_k": {"min": 1, "max": 20}}},
            "out_of_bounds",
            "top_k",
        ),
    ]
    for body, expected, field in cases:
        response = set_installation(roles, **body)
        assert response.status_code == 422, (body, response.text)
        assert code(response) == expected, body
        assert response.json()["error"]["details"]["field"] == field, body


def test_admin_changes_are_recorded_as_security_events(roles: RoleClients, app: FastAPI, clock):
    set_installation(roles, values={"rrf_k": 80, "top_k": 8})

    recorder = SecurityEventRecorder(app.state.engine, clock=clock)
    [event] = [e for e in recorder.recent() if e.event_type == "document_ai_settings_changed"]
    assert event.actor_id == roles.user("admin").id
    assert event.metadata == {
        "version": 1,
        "old": {"rrf_k": 60, "top_k": 10},
        "new": {"rrf_k": 80, "top_k": 8},
    }


# -- Workspace overrides (owners) ------------------------------------------------------


def test_members_see_the_effective_settings_inherited_from_the_installation(roles: RoleClients):
    set_installation(roles, values={"top_k": 7})

    body = roles.client("viewer").get(workspace_path(roles)).json()

    assert body["overrides"] == {}
    assert body["effective"]["top_k"] == 7
    assert body["inherited"]["top_k"] == 7
    assert "rrf_k" not in body["inherited"]  # only the knobs an owner may override
    assert {k["key"] for k in body["knobs"]} == set(body["inherited"])


def test_owners_override_knobs_within_the_admin_bounds(roles: RoleClients):
    set_installation(roles, bounds={"top_k": {"min": 2, "max": 12}})

    ok = set_overrides(roles, {"top_k": 12, "rerank": False, "chunking_strategy": "recursive"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 1
    assert ok.json()["overrides"] == {
        "top_k": 12,
        "rerank": False,
        "chunking_strategy": "recursive",
    }
    assert ok.json()["effective"]["top_k"] == 12

    above = set_overrides(roles, {"top_k": 13})
    assert above.status_code == 422 and code(above) == "out_of_bounds"
    assert above.json()["error"]["details"] == {"field": "top_k", "min": 2, "max": 12}


def test_owners_cannot_override_admin_only_knobs(roles: RoleClients):
    for knob, value in (("rrf_k", 10), ("embed_batch", 4), ("max_pdf_pages", 3)):
        response = set_overrides(roles, {knob: value})
        assert response.status_code == 422, knob
        assert code(response) == "not_overridable"


def test_only_owners_override_and_a_stale_version_is_refused(roles: RoleClients):
    assert set_overrides(roles, {"top_k": 5}, as_role="editor").status_code == 403
    assert set_overrides(roles, {"top_k": 5}, version=0).status_code == 200
    stale = set_overrides(roles, {"top_k": 6}, version=0)
    assert stale.status_code == 409 and code(stale) == "settings_changed"


def test_clearing_an_override_inherits_the_installation_value_again(roles: RoleClients):
    set_overrides(roles, {"top_k": 5})
    set_installation(roles, values={"top_k": 9})

    cleared = set_overrides(roles, {})

    assert cleared.json()["effective"]["top_k"] == 9


def test_tighter_bounds_clamp_an_existing_override(roles: RoleClients):
    set_overrides(roles, {"top_k": 40})

    set_installation(roles, bounds={"top_k": {"min": 1, "max": 15}})

    body = roles.client("owner").get(workspace_path(roles)).json()
    assert body["overrides"]["top_k"] == 40  # kept as the owner set it
    assert body["effective"]["top_k"] == 15


def test_every_override_change_is_audited(roles: RoleClients, app: FastAPI, clock):
    owner = roles.client("owner")
    set_overrides(roles, {"top_k": 5})
    set_overrides(roles, {"top_k": 5})  # no change: no entry
    clock.advance(timedelta(minutes=1))
    set_overrides(roles, {"mmr_lambda": 0.5})

    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="document_ai_settings", entity_id=str(roles.workspace_id)
    )
    feed = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/activity").json()["items"]

    assert [(e.old, e.new) for e in entries] == [
        ({"top_k": None}, {"top_k": 5}),
        ({"mmr_lambda": None, "top_k": 5}, {"mmr_lambda": 0.5, "top_k": None}),
    ]
    assert entries[0].actor_id == roles.user("owner").id
    assert [i["verb"] for i in feed].count("workspace.document_ai_settings_changed") == 2
