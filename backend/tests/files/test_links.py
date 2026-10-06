"""External links and document-to-object links (spec §6.17, stories 65, 66, §4.3).

Behaviour is driven through the HTTP API. Tables and columns come from a real extraction
of the shared sample source, as in the schema browser tests.
"""

from __future__ import annotations

import uuid

import pytest

from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources.test_extraction import add_system, connect, extract


def error_code(response) -> str:
    return response.json()["error"]["code"]


def upload(roles: RoleClients, system: str, name: str = "sad.md") -> str:
    response = roles.client("editor").post(
        f"{system}/files", files={"file": (name, b"# SAD", "text/markdown")}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def source_objects(roles: RoleClients, system: str) -> tuple[str, str]:
    """The id of a table and one of its columns, from the extracted Source Schema."""
    response = roles.client("viewer").get(f"{system}/schema")
    assert response.status_code == 200, response.text
    table = response.json()["tables"][0]
    return table["id"], table["columns"][0]["id"]


@pytest.fixture
def system(roles: RoleClients, sample_source: SampleSource) -> str:
    path = add_system(roles)
    connect(roles, path, sample_source.connection_body())
    extract(roles, path)
    return path


def workspace_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}"


JIRA = {"kind": "jira", "title": "CBS-12", "url": "https://jira.example.com/browse/CBS-12"}


# -- Source links (story 65) -----------------------------------------------------------


def test_an_editor_links_a_repository_a_jira_issue_and_a_confluence_page(
    roles: RoleClients, system: str
):
    bodies = [
        {"kind": "repo", "title": "Core repo", "url": "https://git.example.com/cbs", "note": "ETL"},
        {**JIRA, "note": "Dormant status"},
        {"kind": "confluence", "title": "Design", "url": "https://wiki.example.com/x"},
    ]
    for body in bodies:
        response = roles.client("editor").post(f"{system}/links", json=body)
        assert response.status_code == 201, response.text
        assert response.json()["url"] == body["url"]
        assert response.json()["note"] == body.get("note", "")

    listed = roles.client("viewer").get(f"{system}/links")

    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()["items"]] == ["CBS-12", "Core repo", "Design"]
    assert listed.json()["next_cursor"] is None


def test_links_page_by_cursor(roles: RoleClients, system: str):
    for number in range(3):
        body = {**JIRA, "title": f"Issue {number}"}
        assert roles.client("editor").post(f"{system}/links", json=body).status_code == 201

    first = roles.client("viewer").get(f"{system}/links", params={"limit": 2}).json()
    second = (
        roles.client("viewer")
        .get(f"{system}/links", params={"limit": 2, "cursor": first["next_cursor"]})
        .json()
    )

    titles = [i["title"] for i in first["items"]] + [i["title"] for i in second["items"]]
    assert titles == ["Issue 0", "Issue 1", "Issue 2"]
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "ftp://host/file", "not a url", "https://", "//host/x"]
)
def test_only_http_and_https_urls_are_accepted(roles: RoleClients, system: str, url: str):
    response = roles.client("editor").post(f"{system}/links", json={**JIRA, "url": url})

    assert response.status_code == 422
    assert error_code(response) == "invalid_source_link"


def test_a_link_needs_a_title_and_a_known_kind(roles: RoleClients, system: str):
    editor = roles.client("editor")

    blank = editor.post(f"{system}/links", json={**JIRA, "title": "   "})
    unknown = editor.post(f"{system}/links", json={**JIRA, "kind": "svn"})

    assert blank.status_code == 422
    assert unknown.status_code == 422


def test_a_link_is_edited_and_deleted(roles: RoleClients, system: str):
    link = roles.client("editor").post(f"{system}/links", json=JIRA).json()

    edited = roles.client("editor").patch(
        f"{system}/links/{link['id']}", json={"title": "CBS-13", "note": "moved"}
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "CBS-13" and edited.json()["note"] == "moved"
    assert edited.json()["url"] == JIRA["url"]
    assert roles.client("editor").delete(f"{system}/links/{link['id']}").status_code == 204
    assert roles.client("viewer").get(f"{system}/links").json()["items"] == []
    assert roles.client("editor").delete(f"{system}/links/{link['id']}").status_code == 404


def test_a_viewer_reads_links_but_cannot_change_them(roles: RoleClients, system: str):
    link = roles.client("editor").post(f"{system}/links", json=JIRA).json()
    viewer = roles.client("viewer")

    assert viewer.get(f"{system}/links").status_code == 200
    assert viewer.post(f"{system}/links", json=JIRA).status_code == 403
    assert viewer.patch(f"{system}/links/{link['id']}", json={"note": "x"}).status_code == 403
    assert viewer.delete(f"{system}/links/{link['id']}").status_code == 403


def test_a_link_of_another_workspace_is_not_found(roles: RoleClients, system: str):
    link = roles.client("editor").post(f"{system}/links", json=JIRA).json()
    other = roles.client("non_member").post(
        "/api/v1/workspaces", json={"name": "Other", "description": "", "domain": ""}
    )
    other_system = roles.client("non_member").post(
        f"/api/v1/workspaces/{other.json()['id']}/systems", json={"name": "X", "code": "x"}
    )
    path = f"/api/v1/workspaces/{other.json()['id']}/systems/{other_system.json()['id']}"

    response = roles.client("non_member").delete(f"{path}/links/{link['id']}")

    assert response.status_code == 404


# -- Documents linked to tables and columns (story 66) ---------------------------------


def test_a_document_linked_to_a_table_and_a_column_shows_on_both(roles: RoleClients, system: str):
    table_id, column_id = source_objects(roles, system)
    file_id = upload(roles, system)
    base = f"{workspace_path(roles)}/files/{file_id}/object-links"

    for object_type, object_id in (("table", table_id), ("column", column_id)):
        response = roles.client("editor").post(
            base, json={"object_type": object_type, "object_id": object_id}
        )
        assert response.status_code == 201, response.text

    for object_type, object_id in (("table", table_id), ("column", column_id)):
        documents = roles.client("viewer").get(
            f"{workspace_path(roles)}/objects/{object_type}/{object_id}/documents"
        )
        assert documents.status_code == 200, documents.text
        assert [d["id"] for d in documents.json()["items"]] == [file_id]
        assert documents.json()["items"][0]["name"] == "sad.md"
    listed = roles.client("viewer").get(base).json()["items"]
    assert {(link["object_type"], link["object_id"]) for link in listed} == {
        ("table", table_id),
        ("column", column_id),
    }


def test_linking_the_same_document_twice_changes_nothing(roles: RoleClients, system: str):
    table_id, _ = source_objects(roles, system)
    file_id = upload(roles, system)
    base = f"{workspace_path(roles)}/files/{file_id}/object-links"
    body = {"object_type": "table", "object_id": table_id}

    first = roles.client("editor").post(base, json=body)
    second = roles.client("editor").post(base, json=body)

    assert first.status_code == 201 and second.status_code == 200
    assert len(roles.client("viewer").get(base).json()["items"]) == 1


def test_a_link_is_removed(roles: RoleClients, system: str):
    table_id, _ = source_objects(roles, system)
    file_id = upload(roles, system)
    base = f"{workspace_path(roles)}/files/{file_id}/object-links"
    roles.client("editor").post(base, json={"object_type": "table", "object_id": table_id})

    removed = roles.client("editor").delete(f"{base}/table/{table_id}")

    assert removed.status_code == 204
    documents = roles.client("viewer").get(
        f"{workspace_path(roles)}/objects/table/{table_id}/documents"
    )
    assert documents.json()["items"] == []
    assert roles.client("editor").delete(f"{base}/table/{table_id}").status_code == 404


def test_a_viewer_cannot_link_or_unlink_a_document(roles: RoleClients, system: str):
    table_id, _ = source_objects(roles, system)
    file_id = upload(roles, system)
    base = f"{workspace_path(roles)}/files/{file_id}/object-links"
    viewer = roles.client("viewer")

    posted = viewer.post(base, json={"object_type": "table", "object_id": table_id})
    deleted = viewer.delete(f"{base}/table/{table_id}")

    assert posted.status_code == 403 and deleted.status_code == 403


def test_a_document_links_only_to_objects_and_files_of_its_workspace(
    roles: RoleClients, system: str
):
    table_id, _ = source_objects(roles, system)
    file_id = upload(roles, system)
    base = f"{workspace_path(roles)}/files/{file_id}/object-links"
    editor = roles.client("editor")

    unknown_object = editor.post(
        base, json={"object_type": "table", "object_id": str(uuid.uuid4())}
    )
    column_as_table = editor.post(
        base, json={"object_type": "column", "object_id": str(uuid.uuid4())}
    )
    unknown_file = editor.post(
        f"{workspace_path(roles)}/files/{uuid.uuid4()}/object-links",
        json={"object_type": "table", "object_id": table_id},
    )

    assert unknown_object.status_code == 404
    assert column_as_table.status_code == 404
    assert unknown_file.status_code == 404
