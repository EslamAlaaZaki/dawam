"""Documents reach a model only when the Workspace shares them (AI privacy, #565). These
tests use the production ``WorkspaceDocumentAi`` policy, not a test double: the owner turns
Documents on through ``PUT ai-settings`` and uploads are embedded; with it off nothing is
sent to a model, and documents uploaded earlier are indexed again when it is turned on."""

from __future__ import annotations

from tests.files.test_chunk_context import DOC, headers, light_calls, use_light
from tests.files.test_files import blank_pdf, system, upload
from tests.files.test_indexing import worker  # noqa: F401  (fixture)
from tests.files.test_rerank import documents, order, register_rerank_model, rerank_calls  # noqa: F401
from tests.files.test_search import chunk_rows, register_embedding_model, upload_ok
from tests.files.test_vision_ocr import ocr, use_vision, vision_calls  # noqa: F401
from tests.roles import RoleClients


def share_documents(roles: RoleClients, on: bool):
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
    response = owner.put(path, json={"internal_only": False, "share_documents": on})
    assert response.status_code == 200, response.text
    return response.json()


def reindex_jobs(roles: RoleClients) -> list[dict]:
    jobs = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/jobs").json()
    return [j for j in jobs["items"] if j["type"] == "reindex_documents"]


def test_with_documents_shared_an_upload_is_embedded(roles, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    share_documents(roles, True)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert {(str(m), d) for m, d in chunk_rows(app)} == {(model_id, 4)}
    assert [c for c in fake_llm.calls if c.kind == "embed"]


def test_by_default_nothing_is_sent_to_a_model(roles, fake_llm, app):
    register_embedding_model(roles, internal=True)
    fake_llm.calls.clear()
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert [c for c in fake_llm.calls if c.kind == "embed"] == []
    assert chunk_rows(app) == [(None, None)]


def test_turning_documents_on_indexes_the_documents_uploaded_earlier(roles, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    assert chunk_rows(app) == [(None, None)]
    assert reindex_jobs(roles) == []

    share_documents(roles, True)

    assert {(str(m), d) for m, d in chunk_rows(app)} == {(model_id, 4)}
    assert len(reindex_jobs(roles)) == 1


def test_turning_documents_on_without_documents_queues_nothing(roles, fake_llm):
    share_documents(roles, True)

    assert reindex_jobs(roles) == []


def test_saving_while_documents_stay_on_does_not_index_again(roles, fake_llm):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    share_documents(roles, True)
    share_documents(roles, True)

    assert len(reindex_jobs(roles)) == 1


def test_with_documents_off_an_internal_vision_model_receives_no_page(
    roles,
    ocr,  # noqa: F811
    fake_llm,
    worker,  # noqa: F811
):
    use_vision(roles, internal=True)
    before = len(vision_calls(fake_llm))
    sid = system(roles)

    upload(roles, sid, "scan.pdf", blank_pdf())
    worker()

    assert vision_calls(fake_llm)[before:] == []
    assert ocr.pages == [[1]]


def test_with_documents_off_an_internal_light_model_receives_no_passage(
    roles,
    admin_client,
    fake_llm,
    worker,  # noqa: F811
):
    use_light(roles, admin_client, internal=True)
    sid = system(roles)

    upload(roles, sid, "loans.md", DOC)
    worker()

    assert light_calls(fake_llm) == []
    assert headers(roles) == [None]


def test_with_documents_off_an_internal_rerank_model_receives_no_passage(
    roles,
    fake_llm,
    documents,  # noqa: F811
):
    register_rerank_model(roles, internal=True)
    fake_llm.calls.clear()

    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []
