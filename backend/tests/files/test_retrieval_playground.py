"""The retrieval playground (#383): one search, with every stage's ranking and scores. Driven
through the HTTP API; it runs the real search, so settings and policy apply as there.
"""

from __future__ import annotations

import pytest

from tests.files.test_files import system
from tests.files.test_rerank import register_rerank_model
from tests.files.test_search import policy, register_embedding_model, search, upload_ok
from tests.roles import RoleClients

__all__ = ["policy"]


def explain(roles: RoleClients, q: str, *, as_role="viewer", **params):
    return roles.client(as_role).get(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/search/explain",
        params={"q": q, **params},
    )


@pytest.fixture
def documents(roles: RoleClients) -> None:
    sid = system(roles)
    upload_ok(roles, sid, "a.md", "# Alpha\npostings postings postings")
    upload_ok(roles, sid, "b.md", "# Beta\npostings postings")
    upload_ok(roles, sid, "c.md", "# Gamma\npostings")


def test_full_text_only_shows_the_text_and_fused_stages(roles, policy, fake_llm, documents):
    body = explain(roles, "postings").json()

    assert body["queries"] == ["postings"]
    assert body["vector_used"] is False and body["reranked"] is False
    assert [e["document"] for e in body["full_text"]] == ["a.md", "b.md", "c.md"]
    assert [e["rank"] for e in body["full_text"]] == [1, 2, 3]
    assert all(e["score"] > 0 and e["above_floor"] is not None for e in body["full_text"])
    assert body["vector"] == [] and body["reranked_results"] == []
    assert [e["document"] for e in body["fused"]] == ["a.md", "b.md", "c.md"]
    assert [e["document"] for e in body["final"]] == ["a.md", "b.md", "c.md"]


def test_the_final_stage_is_what_search_returns(roles, policy, fake_llm, documents):
    final = explain(roles, "postings", limit=2).json()["final"]
    real = search(roles, "postings", limit=2).json()["items"]

    assert [(e["document"], e["score"]) for e in final] == [
        (p["document"], p["score"]) for p in real
    ]


def test_vector_and_rerank_stages_appear_when_the_models_are_usable(roles, policy, fake_llm):
    register_embedding_model(roles, internal=True)
    register_rerank_model(roles)
    sid = system(roles)  # uploaded now, so the passages are embedded
    for name, text in [("a", "postings " * 3), ("b", "postings " * 2), ("c", "postings")]:
        upload_ok(roles, sid, f"{name}.md", f"# {name}\n{text}")
    fake_llm.script_rerank([0.1, 0.5, 0.9])

    body = explain(roles, "postings").json()

    assert body["vector_used"] is True and body["reranked"] is True
    assert body["vector"]
    fused = [e["document"] for e in body["fused"]]
    # The model scored the fused order 0.1, 0.5, 0.9: its last pick comes first.
    assert [e["document"] for e in body["reranked_results"]] == fused[::-1]
    assert [e["score"] for e in body["reranked_results"]] == [0.9, 0.5, 0.1]
    assert [e["document"] for e in body["final"]] == fused[::-1]


def test_a_data_level_without_documents_keeps_vectors_and_reranking_out(
    roles, policy, fake_llm, documents
):
    register_embedding_model(roles, internal=False)
    register_rerank_model(roles, internal=False)
    policy.includes_documents = False
    fake_llm.calls.clear()

    body = explain(roles, "postings").json()

    assert body["vector_used"] is False and body["reranked"] is False
    assert body["vector"] == []
    assert [c for c in fake_llm.calls if c.kind in ("embed", "rerank")] == []


def test_every_member_can_explain_and_an_outsider_cannot(roles, policy, fake_llm, documents):
    assert explain(roles, "postings", as_role="viewer").status_code == 200
    assert explain(roles, "postings", as_role="outsider").status_code in (403, 404)
