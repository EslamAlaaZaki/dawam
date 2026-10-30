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
from dawam.modules.files.search import vector_ranking_sql
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
        json={"name": name, "roles": ["embedding"]},
    )
    assert model.status_code == 201, model.text
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


def test_word_forms_match_their_stems_in_arabic_and_english(roles, policy):
    sid = system(roles)
    upload_ok(
        roles,
        sid,
        "forms.md",
        "# Billing\nThe customer reported invoices created yesterday.\n\n"
        "# المدارس\nزارت المدرّسات المدارس والمعلمون.",  # noqa: RUF001
    )

    for query in ("customers report", "creating invoice", "reporting"):
        found = search(roles, query).json()["items"]
        assert [p["section"] for p in found] == ["Billing"], query
    for query in ("المدرسة", "والمدرسون"):
        found = search(roles, query).json()["items"]
        assert [p["section"] for p in found] == ["المدارس"], query


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


def test_the_first_use_of_an_embedding_model_creates_its_hnsw_index_and_queries_use_it(
    roles, policy, fake_llm, app
):
    model_id = register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "ledger.md", "# Ledger\nPostings are booked daily.")
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "risk.md", "# Risk\nExposure is reviewed quarterly.")

    name = f"ix_document_chunks_hnsw_{uuid.UUID(model_id).hex}_4"
    probe = "[0.0,1.0,0.0,0.0]"
    sql = vector_ranking_sql(uuid.UUID(model_id), 4)  # the production query, real binds
    binds = {"w": roles.workspace_id, "v": probe}
    with app.state.engine.connect() as conn:
        indexes = (
            conn.execute(
                sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
            )
            .scalars()
            .all()
        )
        assert len(indexes) == 1 and "USING hnsw" in indexes[0]
        indexed = conn.execute(sa.text(sql), binds).scalars().all()
        conn.execute(sa.text("SET enable_seqscan = off"))
        plan = "\n".join(conn.execute(sa.text("EXPLAIN " + sql), binds).scalars())
        conn.execute(sa.text("RESET enable_seqscan"))
        # The exact scan: the same ranking without the cast, so the index cannot serve it.
        scanned = (
            conn.execute(
                sa.text(sql.replace("(c.embedding::vector(4))", "(c.embedding::text)::vector")),
                binds,
            )
            .scalars()
            .all()
        )
    assert name in plan
    assert indexed == scanned

    # A second upload with the same model reuses the index (no error, still one).
    fake_llm.script_embeddings([0.0, 0.0, 1.0, 0.0])
    upload_ok(roles, sid, "audit.md", "# Audit\nFindings are tracked.")
    with app.state.engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM pg_indexes WHERE indexname = :n"), {"n": name}
        ).scalar()
    assert count == 1


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


def test_only_admins_reindex_every_workspace(roles, policy):
    sid = system(roles)
    upload_ok(roles, sid, "sad.md", "Postings are booked daily.")
    url = "/api/v1/admin/llm/document-ai/reindex"

    assert roles.client("owner").post(url).status_code == 403
    response = roles.client("admin").post(url)

    assert response.status_code == 202, response.text
    assert len(response.json()["job_ids"]) == 1


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


def test_search_matches_small_children_but_returns_their_parent_section_once(roles, policy, app):
    sid = system(roles)
    filler = "Postings are booked and reconciled every day. " * 20  # ~900 characters
    text = f"# Ledger\n\nalpha {filler}\n\nmiddle {filler}\n\nomega {filler}"
    upload_ok(roles, sid, "ledger.md", text)
    assert len(chunk_rows(app, "id")) > 1  # several children were embedded and indexed

    items = search(roles, "alpha omega").json()["items"]

    assert len(items) == 1  # two children matched, their parent is returned once
    assert items[0]["section"] == "Ledger"
    assert "alpha" in items[0]["text"] and "omega" in items[0]["text"]
    assert "middle" in items[0]["text"]
    assert items[0]["document"] == "ledger.md"


def test_limit_counts_distinct_parents_not_matching_children(roles, policy):
    sid = system(roles)
    filler = "Postings are booked and reconciled every day. " * 20  # one passage of its own
    strong = "\n\n".join(f"ledger ledger ledger ledger {filler}" for _ in range(4))
    # Six parents of four strong children are 24 children: more than the first candidate
    # pool holds, and only five parents. The pool must grow to reach seven.
    sections = [f"# Strong {n}\n\n{strong}" for n in range(6)]
    sections += [f"# Weak {n}\n\nledger {filler}" for n in range(2)]
    upload_ok(roles, sid, "big.md", "\n\n".join(sections))

    items = search(roles, "ledger", limit=7).json()["items"]

    assert len({i["section"] for i in items}) == len(items) == 7
    assert all(i["document"] == "big.md" for i in items)


def test_results_from_one_section_are_diversified_by_mmr(roles, policy):
    sid = system(roles)
    filler = "Postings are booked and reconciled every day. " * 20
    strong = "ledger ledger ledger ledger"
    sections = "\n\n".join(f"# Part {n}\n\n{strong} {filler}" for n in range(3))
    upload_ok(roles, sid, "a.md", sections)
    upload_ok(roles, sid, "b.md", f"# Beta\n\nledger ledger {filler}")
    upload_ok(roles, sid, "c.md", f"# Gamma\n\nledger {filler}")

    def documents(lam: float) -> list[str]:
        assert set_document_ai(roles, mmr_lambda=lam).status_code == 200
        return [i["document"] for i in search(roles, "ledger", limit=3).json()["items"]]

    relevance = documents(1.0)
    diverse = documents(0.3)

    assert relevance == ["a.md"] * 3  # by relevance alone, one document's sections fill it
    assert sorted(diverse) == ["a.md", "b.md", "c.md"]


def test_a_query_unlike_every_passage_returns_nothing_at_the_default_floors(
    roles, policy, fake_llm
):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "ledger.md", "# Ledger\nPostings are booked daily.")
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "risk.md", "# Risk\nExposure is reviewed quarterly.")

    fake_llm.script_embeddings([0.0, 0.0, 1.0, 0.0])  # orthogonal to both, no word in common
    assert search(roles, "zebra").json()["items"] == []

    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])  # the same meaning as ledger.md only
    assert [i["document"] for i in search(roles, "zebra").json()["items"]] == ["ledger.md"]


def test_fusion_weights_decide_which_ranking_leads(roles, policy, fake_llm):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "text.md", "# Text\nledger ledger ledger ledger")
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "meaning.md", "# Meaning\nledger")

    def first(**weights: float) -> str:
        assert set_document_ai(roles, mmr_lambda=1.0, **weights).status_code == 200
        fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
        return search(roles, "ledger").json()["items"][0]["document"]

    assert first(fts_weight=5.0, vector_weight=0.1) == "text.md"
    assert first(fts_weight=0.1, vector_weight=5.0) == "meaning.md"


def test_a_passage_indexed_before_parents_existed_is_still_returned(roles, policy, app):
    sid = system(roles)
    upload_ok(roles, sid, "old.md", "# Old\n\nlegacy ledger wording")
    with app.state.engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE document_chunks SET parent_ordinal = NULL, parent_content = NULL")
        )

    [hit] = search(roles, "legacy").json()["items"]

    assert (hit["document"], hit["section"], hit["text"]) == (
        "old.md",
        "Old",
        "legacy ledger wording",
    )


def set_document_ai(roles: RoleClients, **overrides):
    """Override the Workspace's Document AI settings, as its owner."""
    path = f"/api/v1/workspaces/{roles.workspace_id}/document-ai"
    owner = roles.client("owner")
    current = owner.get(path).json()
    return owner.put(
        path,
        json={"version": current["version"], "overrides": {**current["overrides"], **overrides}},
    )


def set_installation_document_ai(roles: RoleClients, **values):
    """Set the installation's Document AI values, as an admin."""
    admin = roles.client("admin")
    current = admin.get(f"{ADMIN}/document-ai").json()
    response = admin.put(
        f"{ADMIN}/document-ai", json={"version": current["version"], "values": values}
    )
    assert response.status_code == 200, response.text


def set_chunking_strategy(roles: RoleClients, strategy: str):
    return set_document_ai(roles, chunking_strategy=strategy)


def test_the_chunking_strategy_is_a_workspace_setting_owners_choose(roles, policy):
    path = f"/api/v1/workspaces/{roles.workspace_id}/document-ai"
    assert roles.client("owner").get(path).json()["effective"]["chunking_strategy"] == "headings"

    chosen = set_chunking_strategy(roles, "recursive").json()
    assert chosen["effective"]["chunking_strategy"] == "recursive"
    assert roles.client("viewer").get(path).json()["effective"]["chunking_strategy"] == "recursive"
    assert set_chunking_strategy(roles, "bogus").status_code == 422


def test_the_passage_size_and_overlap_come_from_the_settings(roles, policy, app):
    sid = system(roles)
    words = " ".join(f"word{n}" for n in range(300))

    def passages(name: str) -> int:
        upload_ok(roles, sid, name, f"# Long\n{words}")
        with app.state.engine.connect() as conn:
            return conn.execute(
                sa.text(
                    "SELECT count(*) FROM document_chunks c JOIN workspace_files f "
                    "ON f.id = c.file_id WHERE f.path LIKE :p"
                ),
                {"p": f"%{name}"},
            ).scalar_one()

    default = passages("default.md")
    assert set_document_ai(roles, chunk_size=60, chunk_overlap=0).status_code == 200

    assert passages("small.md") > default * 2


def test_the_extracted_text_cap_is_an_installation_setting(roles, policy, app):
    set_installation_document_ai(roles, max_extracted_chars=10_000)
    sid = system(roles)

    upload_ok(roles, sid, "long.md", "# Long\n" + "ledger " * 3000)

    with app.state.engine.connect() as conn:
        size = conn.execute(sa.text("SELECT length(extracted_text) FROM workspace_files")).scalar()
    assert size == 10_000


def test_search_returns_the_workspace_top_k_unless_asked_for_another_number(roles, policy):
    sid = system(roles)
    for n in range(6):
        upload_ok(roles, sid, f"doc{n}.md", f"# Part {n}\nledger entry number {n}")
    set_document_ai(roles, top_k=4, mmr_lambda=1.0)

    assert len(search(roles, "ledger").json()["items"]) == 4
    assert len(search(roles, "ledger", limit=2).json()["items"]) == 2


def test_semantic_chunking_embeds_sentences_and_splits_where_the_topic_changes(
    roles, policy, fake_llm, app
):
    # The fake gives one vector per call; a batch of one makes it one per text.
    set_installation_document_ai(roles, embed_batch=1)
    register_embedding_model(roles, internal=True)
    assert set_chunking_strategy(roles, "semantic").status_code == 200
    sid = system(roles)
    cats, stocks = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    fake_llm.script_embeddings(cats, cats, stocks, stocks)  # one per sentence
    fake_llm.script_embeddings(cats, stocks)  # one per passage

    upload_ok(
        roles,
        sid,
        "mix.md",
        "# Mix\nCats purr softly. Cats nap all day. Stocks fell sharply. Stocks rose again.",
    )

    contents = [r[0] for r in chunk_rows(app, "content")]
    assert contents == [
        "Cats purr softly. Cats nap all day.",
        "Stocks fell sharply. Stocks rose again.",
    ]


def test_a_query_naming_an_identifier_ranks_the_passages_containing_it_first(roles, policy):
    sid = system(roles)
    upload_ok(
        roles,
        sid,
        "dictionary.md",
        "# Status codes\nstat cust status stat cust status stat cust lookups.\n\n"
        "# Customer table\nThe cust_stat column holds the status.\n\n"
        "# Other\nstat of the cust.",
    )

    found = search(roles, "cust_stat").json()["items"]

    assert found[0]["section"] == "Customer table"


def test_an_exact_phrase_ranks_above_the_same_words_scattered(roles, policy):
    sid = system(roles)
    upload_ok(
        roles,
        sid,
        "terms.md",
        "# Scattered\nA fee applies. Late arrival is noted. Payment is due; payment, fee, late.\n\n"
        "# Exact\nThe late payment fee is charged monthly.",
    )

    found = search(roles, "late payment fee").json()["items"]

    assert [p["section"] for p in found] == ["Exact", "Scattered"]


def test_an_identifier_match_stays_first_when_vector_hits_rank_above_it(roles, policy, fake_llm):
    register_embedding_model(roles, internal=True)
    sid = system(roles)
    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])
    upload_ok(roles, sid, "ledger.md", "# Ledger\nPostings are booked daily.")
    fake_llm.script_embeddings([1.0, 0.0, 0.0, 0.0])
    upload_ok(roles, sid, "dictionary.md", "# Customer table\nThe cust_stat column.")

    fake_llm.script_embeddings([0.0, 1.0, 0.0, 0.0])  # the query's vector is the ledger's
    found = search(roles, "cust_stat").json()["items"]

    assert found[0]["section"] == "Customer table"


def test_an_identifier_with_other_words_still_ranks_its_passage_first(roles, policy):
    sid = system(roles)
    upload_ok(
        roles,
        sid,
        "dictionary.md",
        "# Status\nThe customer status is reported daily, customer status again.\n\n"
        "# Customer table\nThe cust_stat column.",
    )

    found = search(roles, "customer status cust_stat").json()["items"]

    assert found[0]["section"] == "Customer table"


@pytest.mark.parametrize(
    "q", ["cust_stat & | !", "'cust_stat'", "a:*b", '"late (payment"', "!!", ":*"]
)
def test_operator_like_input_is_searched_as_words_and_never_errors(roles, policy, q):
    sid = system(roles)
    upload_ok(roles, sid, "d.md", "# T\nThe cust_stat column and a late payment.")

    assert search(roles, q).status_code == 200
