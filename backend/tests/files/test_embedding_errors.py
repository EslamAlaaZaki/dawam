"""A document whose passages were not embedded says so (#622): it stays ``indexed`` and is
found by full-text, and its ``embedding_error`` gives the reason (Text only, or partly
embedded). A refused batch is retried passage by passage; an outage is not. Settings that
keep documents from a model are skips, not failures. An indexing job tests an untested
embedding model. Driven through the HTTP API with the scripted fake provider."""

from __future__ import annotations

import sqlalchemy as sa

from dawam.modules.llm import LlmError
from tests.files.test_files import files_path, system
from tests.files.test_search import (  # noqa: F401  (policy: autouse fixture)
    ADMIN,
    chunk_rows,
    policy,
    register_embedding_model,
    search,
    upload_ok,
)
from tests.roles import RoleClients

TWO_SECTIONS = "# A\nPostings are booked daily.\n\n# B\nLedgers close monthly."
REFUSED = "input rejected by content safety screening"


def the_file(roles: RoleClients, system_id: str, name: str = "sad.md") -> dict:
    listed = roles.client("owner").get(files_path(roles, system_id)).json()["items"]
    return {f["name"]: f for f in listed}[name]


def status_row(roles: RoleClients, name: str = "sad.md") -> dict:
    rows = roles.client("viewer").get(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/index-status"
    )
    return {r["name"]: r for r in rows.json()["items"]}[name]


def embed_calls(fake_llm) -> int:
    return len([c for c in fake_llm.calls if c.kind == "embed"])


def model_state(roles: RoleClients, model_id: str) -> dict:
    providers = roles.client("admin").get(f"{ADMIN}/providers").json()
    items = providers["items"] if isinstance(providers, dict) else providers
    return {m["id"]: m for p in items for m in p["models"]}[model_id]


def set_test(app, model_id: str, ok: bool | None, error: str | None = None) -> None:
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE llm_models SET test_ok = :ok, test_error = :e WHERE id = :id"),
            {"ok": ok, "e": error, "id": model_id},
        )


def test_a_refused_batch_is_retried_passage_by_passage(roles, fake_llm, app):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script(LlmError("bad_request", REFUSED))  # the batch of both passages
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])  # the first alone is accepted
    fake_llm.script(LlmError("bad_request", REFUSED))  # the second alone is refused

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    done = the_file(roles, sid)
    assert done["index_status"] == "indexed"
    assert done["embedding_error"] == f"1 of 2 passages could not be embedded: {REFUSED}"
    assert done["text_only"] is False
    assert sorted(d is not None for _, d in chunk_rows(app)) == [False, True]
    row = status_row(roles)
    assert (row["embedding_error"], row["text_only"]) == (done["embedding_error"], False)


def test_every_passage_refused_leaves_the_document_text_only(roles, fake_llm, app):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script(*[LlmError("bad_request", REFUSED)] * 3)

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    done = the_file(roles, sid)
    assert done["index_status"] == "indexed"
    assert (done["embedding_error"], done["text_only"]) == (f"Embedding failed: {REFUSED}", True)
    assert chunk_rows(app) == [(None, None), (None, None)]


def test_an_outage_stops_at_once_and_a_successful_reindex_clears_the_error(roles, fake_llm, app):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    before = embed_calls(fake_llm)
    fake_llm.script(LlmError("invalid_response", "The provider sent no vectors."))

    uploaded = upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    assert embed_calls(fake_llm) == before + 1  # no passage-by-passage retry
    done = the_file(roles, sid)
    assert done["index_status"] == "indexed" and done["text_only"] is True
    assert done["embedding_error"] == "Embedding failed: The provider sent no vectors."
    assert status_row(roles)["text_only"] is True
    assert [p["document"] for p in search(roles, "postings").json()["items"]] == ["sad.md"]

    again = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/{uploaded['id']}/reindex"
    )

    assert again.status_code == 202, again.text
    healed = the_file(roles, sid)
    assert (healed["embedding_error"], healed["text_only"]) == (None, False)
    assert all(m is not None for m, _ in chunk_rows(app))


def test_documents_not_shared_with_the_ai_are_plain_indexed(
    roles,
    fake_llm,
    policy,  # noqa: F811
):
    register_embedding_model(roles, internal=True)
    policy.includes_documents = False
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    done = the_file(roles, sid)
    assert done["index_status"] == "indexed"
    assert (done["embedding_error"], done["text_only"]) == (None, False)


def test_no_embedding_model_or_an_external_one_when_internal_only_is_plain_indexed(
    roles,
    fake_llm,
    policy,  # noqa: F811
):
    sid = system(roles)
    upload_ok(roles, sid, "none.md", TWO_SECTIONS)
    register_embedding_model(roles, internal=False)
    policy.internal_only = True
    upload_ok(roles, sid, "external.md", TWO_SECTIONS)

    for name in ("none.md", "external.md"):
        assert the_file(roles, sid, name)["embedding_error"] is None, name


def test_indexing_tests_an_untested_embedding_model_and_records_the_result(roles, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    set_test(app, model_id, None)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    assert model_state(roles, model_id)["test_ok"] is True
    assert the_file(roles, sid)["embedding_error"] is None
    assert {str(m) for m, _ in chunk_rows(app)} == {model_id}


def test_an_untested_model_that_fails_its_test_leaves_the_document_text_only(roles, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    set_test(app, model_id, None)
    sid = system(roles)
    fake_llm.script(LlmError("auth", "The provider rejected the API key."))

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    state = model_state(roles, model_id)
    assert state["test_ok"] is False and state["test_error"] == "The provider rejected the API key."
    done = the_file(roles, sid)
    assert done["text_only"] is True
    assert done["embedding_error"] == (
        "The embedding model failed its connection test: The provider rejected the API key."
    )


def test_a_model_that_failed_its_test_is_not_tested_again_by_indexing(roles, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    set_test(app, model_id, False, "The key expired.")
    sid = system(roles)
    before = embed_calls(fake_llm)

    upload_ok(roles, sid, "sad.md", TWO_SECTIONS)

    assert embed_calls(fake_llm) == before
    assert model_state(roles, model_id)["test_ok"] is False
    assert the_file(roles, sid)["embedding_error"] == (
        "The embedding model failed its connection test: The key expired."
    )
