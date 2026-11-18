"""Re-embedding on an embedding-model change (ADR 0006): an owner may change a Workspace's
embedding model after documents are indexed; the ``reindex_documents`` job builds the new
vectors while search matches text only, then switches to them all at once. Driven through
the HTTP API and the jobs queue, with the scripted fake provider behind every model."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from dawam.modules.jobs import InlineJobRunner, JobService
from dawam.modules.llm import LlmError
from tests.files.test_files import system
from tests.files.test_search import (  # noqa: F401  (policy: autouse fixture)
    ADMIN,
    chunk_rows,
    policy,
    register_embedding_model,
    search,
    upload_ok,
)
from tests.roles import RoleClients


@pytest.fixture
def worker(roles: RoleClients, jobs: InlineJobRunner, clock):
    """Run queued jobs on demand, as the worker process would (once a test has switched the
    inline runner off)."""
    queue = JobService(roles.app.state.engine, runner=jobs, clock=clock)

    def run_all() -> int:
        ran = 0
        while (job := queue.claim_next()) is not None:
            queue.execute(job)
            ran += 1
        return ran

    return run_all


def ai_settings(roles: RoleClients) -> dict:
    response = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/ai-settings")
    assert response.status_code == 200, response.text
    return response.json()


def choose(roles: RoleClients, model_id: str | None):
    current = ai_settings(roles)
    return roles.client("owner").put(
        f"/api/v1/workspaces/{roles.workspace_id}/ai-settings",
        json={
            "internal_only": current["internal_only"],
            "embedding_model_id": model_id,
        },
    )


def embed_calls(fake_llm) -> int:
    return len([c for c in fake_llm.calls if c.kind == "embed"])


def two_documents(roles: RoleClients, fake_llm) -> str:
    sid = system(roles)
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "ledger.md", "# Ledger\nPostings are booked daily.")
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "risk.md", "# Risk\nExposure is reviewed quarterly.")
    return sid


def test_changing_the_model_rebuilds_the_vectors_with_full_text_search_meanwhile(
    roles, fake_llm, app, jobs, worker
):
    first = register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False

    changed = choose(roles, second)

    assert changed.status_code == 200, changed.text
    assert changed.json()["embedding_model"]["id"] == second
    assert changed.json()["embedding_rebuilding"] is True
    # During the rebuild: the old vectors are kept but unused, text still matches, and the
    # query is not embedded (no vector ranking at all).
    assert {str(m) for m, _ in chunk_rows(app)} == {first}
    before = embed_calls(fake_llm)
    assert [p["document"] for p in search(roles, "postings").json()["items"]] == ["ledger.md"]
    assert search(roles, "zzz").json()["items"] == []
    assert embed_calls(fake_llm) == before

    fake_llm.dimension = 6
    assert worker() == 1

    assert {(str(m), d) for m, d in chunk_rows(app)} == {(second, 6)}
    assert ai_settings(roles)["embedding_rebuilding"] is False
    # After the switch, the new vectors match by meaning (no word in common).
    assert len(search(roles, "zzz").json()["items"]) == 2


def test_a_newer_change_supersedes_a_rebuild_and_the_switch_keeps_one_model(
    roles, fake_llm, app, jobs, worker
):
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    third = register_embedding_model(roles, internal=True, name="e5", use=False)
    jobs.inline = False

    assert choose(roles, second).status_code == 200
    assert choose(roles, third).status_code == 200
    worker()

    assert {str(m) for m, _ in chunk_rows(app)} == {third}
    assert ai_settings(roles)["embedding_rebuilding"] is False


def test_removing_the_embedding_model_drops_the_old_vectors(roles, fake_llm, app):
    first = register_embedding_model(roles, internal=True)
    two_documents(roles, fake_llm)

    assert choose(roles, None).status_code == 200

    assert chunk_rows(app) == [(None, None), (None, None)]
    assert len(search(roles, "postings").json()["items"]) == 1
    # Nothing is indexed with the old model any more, so an admin may remove it.
    assert roles.client("admin").delete(f"{ADMIN}/models/{first}").status_code == 204


def test_a_document_that_fails_to_embed_stays_text_only_and_the_switch_still_happens(
    roles, fake_llm, app, jobs, worker
):
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200

    fake_llm.script(LlmError("bad_request", "refused"))
    worker()

    rows = {str(m) if m else None for m, _ in chunk_rows(app)}
    assert rows <= {second, None} and None in rows
    assert ai_settings(roles)["embedding_rebuilding"] is False
    assert "ledger.md" in [p["document"] for p in search(roles, "postings").json()["items"]]


def test_a_reindex_finishes_a_rebuild_left_behind(roles, fake_llm, app, jobs, worker):
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200
    with app.state.engine.begin() as conn:  # the rebuild's job was lost before it ran
        conn.execute(sa.text("DELETE FROM jobs"))

    started = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/reindex"
    )
    assert started.status_code == 202, started.text
    worker()

    assert {str(m) for m, _ in chunk_rows(app)} == {second}
    assert ai_settings(roles)["embedding_rebuilding"] is False


def test_a_rebuild_whose_job_is_cancelled_while_queued_still_switches(
    roles, fake_llm, app, jobs, worker
):
    register_embedding_model(roles, internal=True, name="bge-small")
    sid = two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200
    listed = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/jobs")
    [job] = [j for j in listed.json()["items"] if j["title"] == "Re-embed documents"]

    cancelled = roles.client("owner").post(f"/api/v1/jobs/{job['id']}/cancel")

    assert cancelled.status_code == 200, cancelled.text
    assert ai_settings(roles)["embedding_rebuilding"] is False
    assert chunk_rows(app) == [(None, None), (None, None)]  # the old model's vectors are gone
    # Vector search is back on, for the new model: a new document is found by meaning.
    upload_ok(roles, sid, "new.md", "# Audit\nControls are tested yearly.")
    worker()
    assert {str(m) for m, _ in chunk_rows(app) if m} == {second}
    assert [p["document"] for p in search(roles, "zzz").json()["items"]] == ["new.md"]


def reindex_jobs(roles: RoleClients) -> list[dict]:
    listed = roles.client("owner").get(f"/api/v1/workspaces/{roles.workspace_id}/jobs")
    return [j for j in listed.json()["items"] if j["type"] == "reindex_documents"]


def reindex_all(roles: RoleClients) -> dict:
    started = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/reindex"
    )
    assert started.status_code == 202, started.text
    return started.json()


def file_errors(app) -> dict[str, str | None]:
    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT path, embedding_error FROM workspace_files"))
        return {path: error for path, error in rows}


def test_reindex_all_while_one_is_queued_joins_it(roles, fake_llm, app, jobs, worker):
    register_embedding_model(roles, internal=True)
    two_documents(roles, fake_llm)
    jobs.inline = False

    first = reindex_all(roles)
    second = reindex_all(roles)

    assert second["job_id"] == first["job_id"]
    assert len(reindex_jobs(roles)) == 1
    assert worker() == 1


def test_a_superseded_rebuild_ends_cancelled_not_succeeded(roles, fake_llm, app, jobs, worker):
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    third = register_embedding_model(roles, internal=True, name="e5", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200
    assert choose(roles, third).status_code == 200

    worker()

    older, newer = sorted(reindex_jobs(roles), key=lambda j: j["status"])
    superseded = "Superseded by a newer re-index, which covers every document."
    assert (older["status"], older["error"]) == ("cancelled", superseded)
    assert superseded in older["log"]
    assert newer["status"] == "succeeded"
    assert {str(m) for m, _ in chunk_rows(app)} == {third}


def test_reindex_all_joins_a_rebuild_and_cancelling_it_marks_what_it_never_reached(
    roles, fake_llm, app, jobs, worker
):
    """The #621 sequence: a model change queues a re-embed, Re-index all is pressed twice
    (it joins that job), then the job is cancelled: nothing reports success, and every
    document left without a vector of the new model says so."""
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200
    [rebuild] = reindex_jobs(roles)

    reindex_all(roles)
    reindex_all(roles)

    assert [j["id"] for j in reindex_jobs(roles)] == [rebuild["id"]]
    cancelled = roles.client("owner").post(f"/api/v1/jobs/{rebuild['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    worker()

    assert [j["status"] for j in reindex_jobs(roles)] == ["cancelled"]
    unreached = (
        "Not embedded: the re-index was cancelled before reaching this document. "
        "Re-index to embed it."
    )
    assert file_errors(app) == {"ledger.md": unreached, "risk.md": unreached}
    assert ai_settings(roles)["embedding_rebuilding"] is False

    reindex_all(roles)
    worker()

    assert file_errors(app) == {"ledger.md": None, "risk.md": None}
    assert {str(m) for m, _ in chunk_rows(app)} == {second}


def test_a_cancelled_rebuild_replaces_a_partly_embedded_note_it_never_reached(
    roles, fake_llm, app, jobs, worker
):
    """A partly-embedded note describes vectors of the old model, which the switch drops:
    a document the cancelled re-embed never reached says it was not reached instead."""
    register_embedding_model(roles, internal=True, name="bge-small")
    two_documents(roles, fake_llm)
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE workspace_files SET embedding_error = "
                "'1 of 2 passages could not be embedded: refused' WHERE path = 'ledger.md'"
            )
        )
    second = register_embedding_model(roles, internal=True, name="bge-large", use=False)
    jobs.inline = False
    assert choose(roles, second).status_code == 200
    [rebuild] = reindex_jobs(roles)

    cancelled = roles.client("owner").post(f"/api/v1/jobs/{rebuild['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    worker()

    unreached = (
        "Not embedded: the re-index was cancelled before reaching this document. "
        "Re-index to embed it."
    )
    assert file_errors(app) == {"ledger.md": unreached, "risk.md": unreached}
