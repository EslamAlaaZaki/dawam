"""Background indexing (the ``index_document`` job): an upload returns at once as
``queued``; the job moves the file to ``indexed``, or to ``failed`` with its error while
other files carry on. Driven through the jobs queue: the inline runner is switched off so
the test decides when the worker runs."""

from __future__ import annotations

import pytest

from dawam.modules.jobs import InlineJobRunner, JobService
from tests.files.test_files import files_path, stored, system, upload
from tests.roles import RoleClients


@pytest.fixture
def worker(roles: RoleClients, jobs: InlineJobRunner, clock):
    """Leave jobs queued, and run them (as the worker process would) on demand."""
    jobs.inline = False

    def run_all() -> int:
        queue = JobService(roles.app.state.engine, runner=jobs, clock=clock)
        ran = 0
        while (job := queue.claim_next()) is not None:
            queue.execute(job)
            ran += 1
        return ran

    return run_all


def listed(roles: RoleClients, system_id: str) -> dict[str, dict]:
    response = roles.client("owner").get(files_path(roles, system_id))
    assert response.status_code == 200, response.text
    return {f["name"]: f for f in response.json()["items"]}


def search(roles: RoleClients, query: str) -> list[dict]:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/search", params={"q": query}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_an_upload_returns_queued_and_the_job_indexes_it(roles: RoleClients, worker):
    system_id = system(roles)

    response = upload(roles, system_id, "ledger.md", b"# Ledger\nDormant accounts close.")

    assert response.status_code == 201, response.text
    assert response.json()["index_status"] == "queued"
    assert listed(roles, system_id)["ledger.md"]["index_status"] == "queued"
    assert search(roles, "dormant") == []

    assert worker() == 1

    done = listed(roles, system_id)["ledger.md"]
    assert done["index_status"] == "indexed"
    assert done["index_error"] is None
    assert done["text_status"] == "extracted"
    assert [p["document"] for p in search(roles, "dormant")] == ["ledger.md"]


def test_a_failing_file_ends_failed_with_its_error_and_the_others_continue(
    roles: RoleClients, worker, monkeypatch
):
    system_id = system(roles)
    upload(roles, system_id, "bad.md", b"# Bad\nbroken")
    upload(roles, system_id, "good.md", b"# Good\nfine notes")

    from dawam.modules.files import search as search_module

    real = search_module.extract_text

    def flaky(file_type, data):
        if b"broken" in data:
            raise RuntimeError("parser exploded")
        return real(file_type, data)

    monkeypatch.setattr(search_module, "extract_text", flaky)

    assert worker() == 2

    files = listed(roles, system_id)
    assert files["bad.md"]["index_status"] == "failed"
    assert "parser exploded" in files["bad.md"]["index_error"]
    assert files["good.md"]["index_status"] == "indexed"
    assert [p["document"] for p in search(roles, "fine")] == ["good.md"]


def test_a_replaced_file_goes_back_to_queued_and_the_stale_job_is_dropped(
    roles: RoleClients, worker
):
    system_id = system(roles)
    first = upload(roles, system_id, "n.md", b"# One\nalpha").json()
    upload(roles, system_id, "n.md", b"# Two\nbeta")  # same name: overwritten in place

    assert worker() == 2

    assert listed(roles, system_id)["n.md"]["index_status"] == "indexed"
    text, _, _ = stored(roles.app, first["id"])
    assert "beta" in text and "alpha" not in text
    assert [p["document"] for p in search(roles, "beta")] == ["n.md"]
    assert search(roles, "alpha") == []


def test_with_the_inline_runner_the_file_is_indexed_by_the_time_it_is_listed(roles: RoleClients):
    system_id = system(roles)

    upload(roles, system_id, "a.md", b"# A\ntext")

    assert listed(roles, system_id)["a.md"]["index_status"] == "indexed"
