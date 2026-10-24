"""Vision-model OCR (#425): a scanned page goes to the Workspace's vision model only when the
Workspace's data-sharing level would let the document's text reach that provider (an internal,
self-hosted model always may). Otherwise, or when the vision call fails, the self-hosted OCR
engine reads it and the file's ``index_note`` says so. Driven through the jobs queue with
fixture files; the self-hosted engine is faked at its port (``Services.ocr_engine``) and the
vision model is the scripted fake provider (``fake_llm``). The page really is rendered."""

from __future__ import annotations

import pytest

from dawam.modules.files.search import VISION_REFUSED
from dawam.modules.llm import LlmError, Reply, Usage
from tests.files import test_indexing
from tests.files.test_files import blank_pdf, system, text_pdf, upload
from tests.files.test_indexing import listed, search
from tests.files.test_ocr import SCANNED, FakeOcr, pdf_of
from tests.files.test_search import Policy
from tests.llm.test_workspace_ai import register
from tests.roles import RoleClients

worker = test_indexing.worker  # the fixture that runs queued jobs on demand

LONG_LINE = "Dormant accounts are closed after twelve months without activity."
ADMIN = "/api/v1/admin/llm"


@pytest.fixture(autouse=True)
def policy(services, fake_llm) -> Policy:
    """Set before ``roles`` builds the app. Documents may not reach a model by default."""
    policy = Policy(includes_documents=False)
    services.document_ai = policy
    return policy


@pytest.fixture
def ocr(app) -> FakeOcr:
    """The self-hosted engine, faked."""
    fake = FakeOcr(app.state.engine, markdown="# Self-hosted\n\nDormant read on our servers.")
    app.state.services.ocr_engine = fake
    return fake


def use_vision(roles: RoleClients, *, internal: bool) -> dict:
    """A tested vision model, chosen as the Workspace's."""
    assert roles.workspace_id  # created first, while there is no model to choose
    model = register(roles.client("admin"), "qwen-vl", internal=internal, roles=("vision",))
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
    current = owner.get(path).json()
    chosen = owner.put(
        path,
        json={
            "internal_only": False,
            "data_sharing_level": current["data_sharing_level"],
            "vision_model_id": model["id"],
        },
    )
    assert chosen.status_code == 200, chosen.text
    return model


def vision_calls(fake_llm) -> list:
    return [c for c in fake_llm.calls if c.messages and c.messages[0].images]


def test_a_self_hosted_vision_model_reads_the_scan_whatever_the_data_sharing_level(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=True)
    before = len(vision_calls(fake_llm))
    fake_llm.script(Reply(text=f"```markdown\n{SCANNED}\n```", usage=Usage(1200, 40)))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())

    assert worker() == 1

    done = listed(roles, system_id)["scan.pdf"]
    assert done["index_status"] == "indexed" and done["text_status"] == "extracted"
    assert done["index_note"] is None
    assert ocr.pages == []
    [call] = vision_calls(fake_llm)[before:]
    [image] = call.messages[0].images
    assert image.media_type == "image/png" and image.data.startswith(b"\x89PNG")
    assert [p["document"] for p in search(roles, "الخاملة")] == ["scan.pdf"]
    usage = roles.client("admin").get(f"{ADMIN}/usage").json()["workspaces"]
    [mine] = [w for w in usage if w["workspace_id"] == str(roles.workspace_id)]
    assert mine["tokens_by_role"]["vision"] == 1240


def test_an_external_vision_model_reads_the_scan_when_documents_may_be_shared(
    roles: RoleClients, ocr, fake_llm, policy, worker
):
    policy.includes_documents = True
    use_vision(roles, internal=False)
    fake_llm.script(Reply(text=SCANNED))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    assert ocr.pages == []
    assert listed(roles, system_id)["scan.pdf"]["index_note"] is None
    assert [p["document"] for p in search(roles, "dormant")] == ["scan.pdf"]


def test_otherwise_the_self_hosted_engine_reads_it_and_the_file_says_so(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=False)
    before = len(vision_calls(fake_llm))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    done = listed(roles, system_id)["scan.pdf"]
    assert done["index_status"] == "indexed"
    assert done["index_note"] == VISION_REFUSED
    assert ocr.pages == [[1]]
    assert vision_calls(fake_llm)[before:] == []
    assert [p["document"] for p in search(roles, "servers")] == ["scan.pdf"]


def test_internal_only_keeps_the_scan_away_from_an_external_vision_model(
    roles: RoleClients, ocr, fake_llm, policy, worker
):
    policy.includes_documents, policy.internal_only = True, True
    use_vision(roles, internal=False)
    before = len(vision_calls(fake_llm))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    assert vision_calls(fake_llm)[before:] == []
    assert ocr.pages == [[1]]
    assert listed(roles, system_id)["scan.pdf"]["index_note"] == VISION_REFUSED


def test_a_pdf_with_enough_text_needs_no_ocr_and_gets_no_note(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=False)
    system_id = system(roles)
    upload(roles, system_id, "typed.pdf", text_pdf(LONG_LINE))
    worker()

    assert ocr.pages == []
    assert listed(roles, system_id)["typed.pdf"]["index_note"] is None


def test_only_the_pages_short_of_text_go_to_the_vision_model(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=True)
    before = len(vision_calls(fake_llm))
    fake_llm.script(Reply(text="Page two, read by the model."))
    system_id = system(roles)
    upload(roles, system_id, "mixed.pdf", pdf_of(text_pdf(LONG_LINE), blank_pdf()))
    worker()

    assert len(vision_calls(fake_llm)[before:]) == 1
    assert [p["document"] for p in search(roles, "twelve months")] == ["mixed.pdf"]
    assert [p["document"] for p in search(roles, "read by the model")] == ["mixed.pdf"]


def test_a_failing_vision_model_falls_back_to_the_self_hosted_engine(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=True)
    fake_llm.script(LlmError("bad_request", "The model refused the image."))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    done = listed(roles, system_id)["scan.pdf"]
    assert done["index_status"] == "indexed"
    assert ocr.pages == [[1]]
    assert "The model refused the image." in done["index_note"]
    assert "self-hosted OCR engine read" in done["index_note"]


def test_an_exhausted_budget_falls_back_to_the_self_hosted_engine(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=True)
    admin = roles.client("admin")
    budget = admin.put(f"{ADMIN}/budgets/installation", json={"monthly_token_budget": 0})
    assert budget.status_code == 200, budget.text
    before = len(vision_calls(fake_llm))
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()

    assert vision_calls(fake_llm)[before:] == []
    assert ocr.pages == [[1]]
    assert "budget" in listed(roles, system_id)["scan.pdf"]["index_note"]


def test_the_note_is_cleared_when_the_file_is_indexed_again(
    roles: RoleClients, ocr, fake_llm, worker
):
    use_vision(roles, internal=False)
    system_id = system(roles)
    upload(roles, system_id, "scan.pdf", blank_pdf())
    worker()
    file_id = listed(roles, system_id)["scan.pdf"]["id"]

    replaced = roles.client("editor").put(
        f"/api/v1/workspaces/{roles.workspace_id}/files/{file_id}/replace",
        files={"file": ("scan.pdf", text_pdf(LONG_LINE), "application/octet-stream")},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["index_note"] is None
    worker()

    assert listed(roles, system_id)["scan.pdf"]["index_note"] is None
