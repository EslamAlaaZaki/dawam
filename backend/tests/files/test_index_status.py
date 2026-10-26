"""The index status page (24.3): per file, where indexing is, how many passages it has, how
many are embedded, how many were embedded by an older model, and why it failed; owners and
editors index one file or all again. Driven through the HTTP API with the fake provider."""

from __future__ import annotations

import sqlalchemy as sa

from tests.files.test_files import system
from tests.files.test_search import (  # noqa: F401  (policy: autouse fixture)
    policy,
    register_embedding_model,
    upload_ok,
)


def status_url(roles) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/documents/index-status"


def rows(roles, as_role="viewer") -> dict:
    response = roles.client(as_role).get(status_url(roles))
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()["items"]}


def test_the_page_matches_each_files_real_status(roles):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    upload_ok(
        roles, sid, "sad.md", "# A\nPostings are booked daily.\n\n# B\nLedgers close monthly."
    )

    item = rows(roles)["sad.md"]

    assert item["index_status"] == "indexed"
    assert item["index_error"] is None
    assert item["chunk_count"] >= 1
    assert item["embedded_count"] == item["chunk_count"]
    assert item["stale_count"] == 0


def test_text_only_files_have_no_embedding_coverage(roles):
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    item = rows(roles)["sad.md"]

    assert item["chunk_count"] >= 1
    assert item["embedded_count"] == 0


def test_vectors_of_an_older_model_are_stale(roles, app):
    register_embedding_model(roles, internal=True, name="bge-small")
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    # A model change re-embeds at once; stand in for a re-embed not yet done.
    with app.state.engine.begin() as conn:
        conn.execute(sa.text("UPDATE document_chunks SET embedding_model_id = gen_random_uuid()"))

    item = rows(roles)["sad.md"]

    assert item["stale_count"] == item["chunk_count"] >= 1


def test_a_failed_file_shows_its_error(roles, app):
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE workspace_files SET index_status='failed', index_error='boom'")
        )

    item = rows(roles)["sad.md"]

    assert (item["index_status"], item["index_error"]) == ("failed", "boom")


def test_reindexing_one_file_indexes_it_again(roles, app):
    sid = system(roles)
    file = upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE workspace_files SET index_status='failed', index_error='boom'")
        )
        conn.execute(sa.text("DELETE FROM document_chunks"))

    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/{file['id']}/reindex"
    )

    assert response.status_code == 202, response.text
    item = rows(roles)["sad.md"]
    assert (item["index_status"], item["index_error"]) == ("indexed", None)
    assert item["chunk_count"] >= 1


def test_only_members_who_manage_files_reindex_a_file(roles):
    sid = system(roles)
    file = upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    url = f"/api/v1/workspaces/{roles.workspace_id}/documents/{file['id']}/reindex"

    assert roles.client("viewer").post(url).status_code == 403
    assert roles.client("non_member").post(url).status_code in (403, 404)
    assert roles.client("owner").post(url).status_code == 202


def test_an_unknown_file_is_not_found(roles):
    url = (
        f"/api/v1/workspaces/{roles.workspace_id}/documents/"
        "00000000-0000-0000-0000-000000000000/reindex"
    )
    assert roles.client("owner").post(url).status_code == 404


def test_non_members_do_not_see_the_page(roles):
    assert roles.client("non_member").get(status_url(roles)).status_code in (403, 404)
