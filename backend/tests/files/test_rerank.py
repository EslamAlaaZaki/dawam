"""Reranking the fused search results (#373): with a rerank model assigned to the Workspace,
the top passages are reordered by the model's scores; without one, search behaves as before.
The call goes through the ``MeteredGateway`` (budgets, internal-only, usage), driven through the
HTTP API with the scripted fake provider (``fake_llm``).
"""

from __future__ import annotations

import pytest

from dawam.modules.llm import LlmError
from tests.files.test_files import system
from tests.files.test_search import ADMIN, Policy, policy, search, set_document_ai, upload_ok
from tests.roles import RoleClients

__all__ = ["policy"]  # the autouse fixture, shared


def register_rerank_model(
    roles: RoleClients, *, internal: bool = True, name: str = "bge-reranker-v2-m3"
) -> str:
    """Register a tested rerank model and make it the Workspace's."""
    assert roles.workspace_id  # created first, while there is no model to choose
    admin = roles.client("admin")
    provider = admin.post(
        f"{ADMIN}/providers",
        json={"name": f"p-{name}", "base_url": "http://rerank:8000/v1", "internal": internal},
    )
    assert provider.status_code == 201, provider.text
    model = admin.post(
        f"{ADMIN}/providers/{provider.json()['id']}/models",
        json={"name": name, "roles": ["rerank"]},
    )
    assert model.status_code == 201, model.text
    tested = admin.post(f"{ADMIN}/models/{model.json()['id']}/test")
    assert tested.json()["test_ok"] is True, tested.text
    owner = roles.client("owner")
    path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
    current = owner.get(path).json()
    chosen = owner.put(
        path,
        json={
            "internal_only": current["internal_only"],
            "data_sharing_level": current["data_sharing_level"],
            "rerank_model_id": model.json()["id"],
        },
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["rerank_model"]["name"] == name
    return model.json()["id"]


@pytest.fixture
def documents(roles: RoleClients) -> None:
    """Three documents that all match "postings", in the order full-text ranks them."""
    sid = system(roles)
    upload_ok(roles, sid, "a.md", "# Alpha\npostings postings postings")
    upload_ok(roles, sid, "b.md", "# Beta\npostings postings")
    upload_ok(roles, sid, "c.md", "# Gamma\npostings")


def order(roles: RoleClients) -> list[str]:
    return [p["document"] for p in search(roles, "postings").json()["items"]]


def rerank_calls(fake_llm):
    return [c for c in fake_llm.calls if c.kind == "rerank"]


def test_without_a_rerank_model_search_keeps_the_fused_order(roles, policy, fake_llm, documents):
    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []


def test_results_are_reordered_by_the_rerank_models_scores(roles, policy, fake_llm, documents):
    register_rerank_model(roles)
    fake_llm.calls.clear()
    fake_llm.script_rerank([0.1, 0.5, 0.9])  # the model prefers the last passage

    items = search(roles, "postings").json()["items"]

    assert [p["document"] for p in items] == ["c.md", "b.md", "a.md"]
    assert [p["score"] for p in items] == [0.9, 0.5, 0.1]
    [call] = rerank_calls(fake_llm)
    assert call.query == "postings" and len(call.texts) == 3
    assert call.texts[0].startswith("postings postings postings")


def test_an_owner_can_switch_reranking_off(roles, policy, fake_llm, documents):
    register_rerank_model(roles)
    assert set_document_ai(roles, rerank=False).status_code == 200
    fake_llm.calls.clear()

    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []


def test_the_limit_applies_after_reranking(roles, policy, fake_llm, documents):
    register_rerank_model(roles)
    fake_llm.script_rerank([0.1, 0.5, 0.9])

    items = search(roles, "postings", limit=1).json()["items"]

    assert [p["document"] for p in items] == ["c.md"]


def test_rerank_usage_is_tracked_under_its_role_and_counts_towards_budgets(
    roles, policy, fake_llm, documents
):
    register_rerank_model(roles)

    search(roles, "postings")

    usage = roles.client("admin").get(f"{ADMIN}/usage").json()
    [row] = usage["workspaces"]
    assert row["tokens_by_role"].get("rerank", 0) > 0


def test_an_exhausted_token_budget_keeps_the_fused_order(roles, policy, fake_llm, documents):
    register_rerank_model(roles)
    budget = roles.client("admin").put(
        f"{ADMIN}/budgets/installation", json={"monthly_token_budget": 0}
    )
    assert budget.status_code == 200, budget.text
    fake_llm.calls.clear()
    fake_llm.script_rerank([0.1, 0.5, 0.9])

    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []


def test_a_failing_rerank_model_keeps_the_fused_order(roles, policy, fake_llm, documents):
    register_rerank_model(roles)
    fake_llm.script_rerank(LlmError("auth", "The provider rejected the API key."))

    assert order(roles) == ["a.md", "b.md", "c.md"]


def test_an_internal_rerank_model_may_see_documents_whatever_the_level(
    roles, policy, fake_llm, documents
):
    register_rerank_model(roles, internal=True)
    policy.includes_documents = False
    fake_llm.script_rerank([0.1, 0.5, 0.9])

    assert order(roles) == ["c.md", "b.md", "a.md"]


def test_an_external_rerank_model_needs_the_documents_level(roles, policy, fake_llm, documents):
    register_rerank_model(roles, internal=False)
    policy.includes_documents = False
    fake_llm.calls.clear()

    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []


def test_an_internal_only_workspace_never_reranks_on_an_external_provider(
    roles, policy: Policy, fake_llm, documents
):
    register_rerank_model(roles, internal=False)
    policy.internal_only = True
    fake_llm.calls.clear()

    assert order(roles) == ["a.md", "b.md", "c.md"]
    assert rerank_calls(fake_llm) == []


def test_only_a_model_enabled_for_the_rerank_role_can_be_chosen(roles, policy, fake_llm):
    assert roles.workspace_id
    admin = roles.client("admin")
    provider = admin.post(
        f"{ADMIN}/providers", json={"name": "p", "base_url": "http://x:1/v1", "internal": True}
    ).json()
    model = admin.post(
        f"{ADMIN}/providers/{provider['id']}/models", json={"name": "m", "roles": ["light"]}
    ).json()
    path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
    current = roles.client("owner").get(path).json()

    response = roles.client("owner").put(
        path,
        json={
            "internal_only": current["internal_only"],
            "data_sharing_level": current["data_sharing_level"],
            "rerank_model_id": model["id"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_model_role"
