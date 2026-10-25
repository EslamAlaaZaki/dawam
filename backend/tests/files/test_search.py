"""Document search (spec §6.17, story 143): Postgres full-text with Arabic normalisation,
pgvector embeddings only where the Workspace allows them, hybrid ranking, cited passages
and re-indexing. Driven through the HTTP API with the scripted fake provider behind every
LLM provider (``fake_llm``); the Workspace AI settings are a test policy object.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa

from dawam.modules.files import DocumentAiSettings
from tests.files.test_files import system, upload
from tests.roles import RoleClients

ADMIN = "/api/v1/admin/llm"


@dataclass
class Policy:
    """What the Workspace's AI settings say (stands in for the settings of ticket #40)."""

    includes_documents: bool = True
    internal_only: bool = False

    def document_settings(self, workspace_id: uuid.UUID) -> DocumentAiSettings:
        return DocumentAiSettings(self.includes_documents, self.internal_only)


@pytest.fixture(autouse=True)
def policy(services, fake_llm) -> Policy:
    """Autouse, so it exists before ``roles`` builds the app with it."""
    policy = Policy()
    services.document_ai = policy
    return policy


def register_embedding_model(
    roles: RoleClients, *, internal: bool, name: str = "bge", use: bool = True
) -> str:
    """Register a tested embedding model and, with ``use``, make it the Workspace's."""
    assert roles.workspace_id  # created first, while there is no model to choose
    admin = roles.client("admin")
    provider = admin.post(
        f"{ADMIN}/providers",
        json={"name": f"p-{name}", "base_url": "http://embed:8000/v1", "internal": internal},
    )
    assert provider.status_code == 201, provider.text
    model = admin.post(
        f"{ADMIN}/providers/{provider.json()['id']}/models",
        json={"name": name},
    )
    assert model.status_code == 201, model.text
    roled = admin.put(f"{ADMIN}/models/{model.json()['id']}/roles", json={"roles": ["embedding"]})
    assert roled.status_code == 200, roled.text
    tested = admin.post(f"{ADMIN}/models/{model.json()['id']}/test")
    assert tested.json()["test_ok"] is True, tested.text
    if use:
        owner = roles.client("owner")
        path = f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"
        current = owner.get(path).json()
        chosen = owner.put(
            path,
            json={
                "internal_only": current["internal_only"],
                "data_sharing_level": current["data_sharing_level"],
                "embedding_model_id": model.json()["id"],
            },
        )
        assert chosen.status_code == 200, chosen.text
    return model.json()["id"]


def search(roles: RoleClients, q: str, *, as_role="viewer", **params):
    return roles.client(as_role).get(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/search", params={"q": q, **params}
    )


def chunk_rows(app, columns: str = "embedding_model_id, embedding_dimension"):
    with app.state.engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT {columns} FROM document_chunks")).all()


def upload_ok(roles: RoleClients, system_id: str, name: str, text: str):
    response = upload(roles, system_id, name, text.encode())
    assert response.status_code == 201, response.text
    return response.json()


def test_arabic_text_is_found_whatever_the_diacritics_and_letter_forms(roles, policy):
    sid = system(roles)
    upload_ok(
        roles,
        sid,
        "security.md",
        "# سياسة الأمن\nالمُدَرِّسَةُ تُغَيِّرُ كَلِمَةَ المُرُورِ كُلَّ شهر.\n\n# Ledger\nPostings are booked daily.",  # noqa: RUF001
    )

    for query in ("المدرسه", "المُدَرِّسَة", "كلمه المرور", "المدرسة"):
        found = search(roles, query).json()["items"]
        assert [(p["document"], p["section"]) for p in found] == [("security.md", "سياسة الأمن")], (
            query
        )

    ya = search(roles, "مستشفى")  # alef maqsura in the query, nothing indexed with it
    assert ya.status_code == 200 and ya.json()["items"] == []


def test_results_are_cited_passages_and_any_member_may_search(roles, policy):
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "# Ledger\nPostings are booked daily.\n\n# Risk\nNothing.")

    response = search(roles, "postings booked", as_role="viewer")

    assert response.status_code == 200, response.text
    [hit] = response.json()["items"]
    assert hit["document"] == "sad.md" and hit["section"] == "Ledger"
    assert "Postings are booked daily." in hit["text"] and hit["source_system_id"] == sid
    assert search(roles, "postings", system_id=str(uuid.uuid4())).json()["items"] == []
    assert search(roles, "postings", as_role="non_member").status_code in (403, 404)
    assert search(roles, "postings", as_role="anonymous").status_code == 401
    assert search(roles, "   ").json()["items"] == []


def test_an_exhausted_token_budget_blocks_embedding_but_full_text_search_still_works(
    roles, policy, fake_llm, app
):
    register_embedding_model(roles, internal=True)
    budget = roles.client("admin").put(
        "/api/v1/admin/llm/budgets/installation", json={"monthly_token_budget": 0}
    )
    assert budget.status_code == 200, budget.text
    fake_llm.calls.clear()
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert len(search(roles, "postings").json()["items"]) == 1
    assert [c for c in fake_llm.calls if c.kind == "embed"] == []
    assert chunk_rows(app) == [(None, None)]


def test_embedding_calls_count_towards_the_workspace_usage(roles, policy, fake_llm):
    register_embedding_model(roles, internal=True)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    usage = roles.client("admin").get("/api/v1/admin/llm/usage").json()
    [row] = usage["workspaces"]
    assert row["workspace_id"] == str(roles.workspace_id)
    assert row["tokens_by_role"].get("embedding", 0) > 0


def test_uploading_a_file_again_replaces_its_passages(roles, policy):
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "old ledger wording")
    upload_ok(roles, sid, "sad.md", "new journal wording")

    assert search(roles, "ledger").json()["items"] == []
    assert len(search(roles, "journal").json()["items"]) == 1


def test_without_the_documents_level_only_full_text_is_used(roles, policy, fake_llm, app):
    policy.includes_documents = False
    register_embedding_model(roles, internal=True)
    fake_llm.calls.clear()
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert len(search(roles, "postings").json()["items"]) == 1
    assert [c for c in fake_llm.calls if c.kind == "embed"] == []
    assert chunk_rows(app) == [(None, None)]


def test_documents_are_embedded_and_found_by_meaning_when_the_workspace_allows(
    roles, policy, fake_llm, app
):
    model_id = register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "ledger.md", "# Ledger\nPostings are booked daily.")
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "risk.md", "# Risk\nExposure is reviewed quarterly.")
    assert {(str(m), d) for m, d in chunk_rows(app)} == {(model_id, 4)}

    # No word in common with either document: only the query's vector can match.
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    hits = search(roles, "zzz").json()["items"]

    assert (hits[0]["document"], hits[0]["section"]) == ("risk.md", "Risk")


def test_an_internal_only_workspace_never_sends_embeddings_to_an_external_provider(
    roles, policy, fake_llm, app
):
    policy.internal_only = True
    register_embedding_model(roles, internal=False)
    fake_llm.calls.clear()
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    hits = search(roles, "postings").json()["items"]

    assert len(hits) == 1
    assert [c for c in fake_llm.calls if c.kind == "embed"] == []
    assert chunk_rows(app) == [(None, None)]


def test_an_internal_only_workspace_may_embed_with_an_internal_provider(
    roles, policy, fake_llm, app
):
    policy.internal_only = True
    model_id = register_embedding_model(roles, internal=True)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert [(str(m), d) for m, d in chunk_rows(app)] == [(model_id, 4)]


def test_reindexing_embeds_again_with_the_new_model_and_dimension(roles, policy, fake_llm, app):
    first = register_embedding_model(roles, internal=True, name="bge-small")
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    assert [(str(m), d) for m, d in chunk_rows(app)] == [(first, 4)]

    admin = roles.client("admin")
    # Documents indexed with a model block its removal; drop their link to stand in for the
    # model having gone away some other way (e.g. a database restore).
    with app.state.engine.begin() as conn:
        conn.execute(sa.text("UPDATE document_chunks SET embedding_model_id = NULL"))
    fake_llm.dimension = 6
    second = register_embedding_model(roles, internal=True, name="bge-large")
    assert admin.delete(f"{ADMIN}/models/{first}").status_code == 204
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/documents/reindex"
    )

    assert response.status_code == 202, response.text
    assert [(str(m), d) for m, d in chunk_rows(app)] == [(second, 6)]
    assert len(search(roles, "postings").json()["items"]) == 1


def test_reindexing_follows_the_policy_and_drops_embeddings_that_are_no_longer_allowed(
    roles, policy, fake_llm, app
):
    register_embedding_model(roles, internal=False)
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    assert chunk_rows(app)[0][0] is not None

    policy.internal_only = True
    roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/documents/reindex")

    assert chunk_rows(app) == [(None, None)]


def test_only_owners_and_editors_reindex(roles, policy):
    url = f"/api/v1/workspaces/{roles.workspace_id}/documents/reindex"
    assert roles.client("viewer").post(url).status_code == 403
    assert roles.client("non_member").post(url).status_code in (403, 404)
    assert roles.client("owner").post(url).status_code == 202


def _soft_delete(roles: RoleClients, system_id: str) -> None:
    response = roles.client("owner").delete(
        f"/api/v1/workspaces/{roles.workspace_id}/systems/{system_id}"
    )
    assert response.status_code == 204, response.text


def test_a_soft_deleted_systems_documents_are_not_found(roles, policy):
    kept = system(roles)
    gone = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "CRM", "code": "crm"}
    )
    assert gone.status_code == 201, gone.text
    upload_ok(roles, kept, "kept.md", "# Ledger\nPostings are booked daily.")
    upload_ok(roles, gone.json()["id"], "gone.md", "# Ledger\nPostings are archived yearly.")

    _soft_delete(roles, gone.json()["id"])

    assert [p["document"] for p in search(roles, "postings").json()["items"]] == ["kept.md"]


def test_a_soft_deleted_systems_documents_are_not_found_by_meaning(roles, policy, fake_llm):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "risk.md", "# Risk\nExposure is reviewed quarterly.")
    _soft_delete(roles, sid)

    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    assert search(roles, "zzz").json()["items"] == []


def test_documents_are_indexed_with_the_workspaces_own_embedding_model(
    roles, policy, fake_llm, app
):
    register_embedding_model(roles, internal=True, name="bge-small", use=False)
    chosen = register_embedding_model(roles, internal=True, name="bge-large")
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert [str(m) for m, _ in chunk_rows(app)] == [chosen]


def test_a_workspace_without_an_embedding_model_indexes_text_only(roles, policy, fake_llm, app):
    register_embedding_model(roles, internal=True, use=False)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert chunk_rows(app) == [(None, None)]
    assert len(search(roles, "postings").json()["items"]) == 1


def test_an_embedding_model_with_indexed_documents_cannot_be_removed(roles, policy, fake_llm, app):
    model_id = register_embedding_model(roles, internal=True)
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    assert {str(m) for m, _ in chunk_rows(app)} == {model_id}
    spare = register_embedding_model(roles, internal=True, name="spare", use=False)
    admin = roles.client("admin")

    replacement = {"workspace_id": str(roles.workspace_id), "embedding_model_id": spare}
    refused = admin.post(
        f"{ADMIN}/models/{model_id}/removal", json={"workspace_replacements": [replacement]}
    )
    unticked = admin.put(
        f"{ADMIN}/models/{model_id}/roles",
        json={"roles": [], "workspace_replacements": [replacement]},
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "embedding_model_indexed"
    [named] = refused.json()["error"]["details"]["workspaces"]
    assert named["id"] == str(roles.workspace_id)
    assert unticked.status_code == 409
    assert unticked.json()["error"]["code"] == "embedding_model_indexed"
    assert admin.get(f"{ADMIN}/models/{model_id}/usage").json()["indexed_workspaces"]


def test_chunking_never_asks_the_provider_to_count_tokens(roles, policy, fake_llm, app):
    def remote_count(text: str) -> int:
        raise AssertionError("chunking called the provider's token count")

    fake_llm.token_counter = remote_count
    register_embedding_model(roles, internal=True)
    sid = system(roles)

    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")

    assert len(chunk_rows(app)) == 1


def test_whitespace_only_text_is_not_embedded(roles, policy, fake_llm, app):
    register_embedding_model(roles, internal=True)
    sid = system(roles)

    upload_ok(roles, sid, "blank.md", "  \n\n  ")

    assert [c for c in fake_llm.calls if c.kind == "embed" and c.texts != ("ping",)] == []
