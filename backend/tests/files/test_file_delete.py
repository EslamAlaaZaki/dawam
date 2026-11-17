"""Deleting a file (spec §4.3 "Upload, edit, delete Workspace files"): it leaves the file
area with its stored bytes and, for a document, its search passages and their embeddings."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa

from tests.files.test_files import files_path, other_workspace, system
from tests.files.test_search import (  # noqa: F401  (policy: autouse fixture)
    policy,
    register_embedding_model,
    search,
    upload_ok,
)
from tests.roles import RoleClients


def file_url(roles: RoleClients, file_id: str, workspace_id=None) -> str:
    return f"/api/v1/workspaces/{workspace_id or roles.workspace_id}/files/{file_id}"


def passages_of(app, file_id: str) -> list[tuple]:
    with app.state.engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT embedding IS NOT NULL FROM document_chunks WHERE file_id = :id"),
            {"id": file_id},
        ).all()


def test_an_editor_deletes_a_document_with_its_passages_embeddings_and_bytes(
    roles: RoleClients, app, tmp_path: Path
):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    gone = upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    kept = upload_ok(roles, sid, "mdm.md", "Postings are reconciled monthly.")
    assert passages_of(app, gone["id"]) == [(True,)]

    response = roles.client("editor").delete(file_url(roles, gone["id"]))

    assert response.status_code == 204, response.text
    listed = roles.client("viewer").get(files_path(roles, sid)).json()["items"]
    assert [f["name"] for f in listed] == ["mdm.md"]
    assert roles.client("viewer").get(f"{file_url(roles, gone['id'])}/download").status_code == 404
    assert passages_of(app, gone["id"]) == []
    assert passages_of(app, kept["id"]) == [(True,)]
    assert [p["document"] for p in search(roles, "postings").json()["items"]] == ["mdm.md"]
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


def test_a_deletion_is_recorded_in_the_activity_feed(roles: RoleClients, clock):
    sid = system(roles)
    doc = upload_ok(roles, sid, "sad.md", "Postings.")
    clock.advance(timedelta(minutes=1))

    roles.client("editor").delete(file_url(roles, doc["id"]))

    feed = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/activity").json()
    events = [(i["verb"], i["object_label"]) for i in feed["items"] if i["object_type"] == "file"]
    assert events == [("file.deleted", "sad.md"), ("file.uploaded", "sad.md")]


def test_a_viewer_cannot_delete_and_another_workspace_cannot_reach_the_file(
    roles: RoleClients,
):
    sid = system(roles)
    doc = upload_ok(roles, sid, "sad.md", "Postings.")

    assert roles.client("viewer").delete(file_url(roles, doc["id"])).status_code == 403
    other = other_workspace(roles)
    elsewhere = roles.client("non_member").delete(file_url(roles, doc["id"], other["id"]))
    assert elsewhere.status_code == 404
    assert roles.client("editor").delete(file_url(roles, doc["id"])).status_code == 204
    assert roles.client("editor").delete(file_url(roles, doc["id"])).status_code == 404
