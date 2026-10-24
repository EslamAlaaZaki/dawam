"""Searching uploaded documents: full-text, plus vectors when the Workspace allows them.

Every document with extracted text is cut into passages (``document_chunks``). Each
passage is indexed with Postgres full-text search after Arabic normalisation (the query
gets the same treatment). It is also embedded into pgvector, but only when the
Workspace's data-sharing level includes documents and the embedding model passes the
Workspace's internal-only check (spec §6.17, §6.18); otherwise that Workspace is
searched with full-text only and nothing is sent to a model. Search fuses both rankings
(reciprocal rank fusion) and returns cited passages: the document and its section.

Which data level and internal-only setting a Workspace has is not this module's to know:
it asks a ``DocumentAiPolicy``, and which embedding model is registered it asks an
``EmbeddingModels``; the composition root chooses both.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobCancelledError, JobContext, JobRunner, JobService
from dawam.modules.llm import (
    LlmError,
    MeteredGateway,
    ProviderService,
    RoleService,
    workspace_embedding_model_id,
)
from dawam.modules.sources import hidden_source_system_ids
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.storage import FileStorage

from .internal.arabic import normalise, words
from .internal.chunking import chunk
from .internal.content import detect, extract_text
from .tables import DocumentChunkRecord, WorkspaceFileRecord

logger = logging.getLogger(__name__)

REINDEX_JOB = "reindex_documents"
INDEX_JOB = "index_document"
INDEX_ERROR_MAX_LENGTH = 1000
EMBED_BATCH = 32
CANDIDATES = 20
"""How many passages each ranking contributes before they are fused."""
RRF_K = 60
DEFAULT_LIMIT = 10


@dataclass(frozen=True)
class DocumentAiSettings:
    """What a Workspace's AI settings say about documents."""

    includes_documents: bool
    """The data-sharing level is *documents* or above."""
    internal_only: bool
    """The Workspace may use internal providers only."""


class DocumentAiPolicy(Protocol):
    def document_settings(self, workspace_id: uuid.UUID) -> DocumentAiSettings: ...


class NoDocumentAi:
    """The policy when no Workspace may share documents with a model: full-text only."""

    def document_settings(self, workspace_id: uuid.UUID) -> DocumentAiSettings:
        return DocumentAiSettings(includes_documents=False, internal_only=True)


@dataclass(frozen=True)
class EmbeddingModel:
    id: uuid.UUID
    is_internal: bool
    gateway: MeteredGateway
    """Metered: the token budgets are checked before every embedding call."""


class EmbeddingModels(Protocol):
    def current(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> EmbeddingModel | None:
        """The Workspace's embedding model (calls are attributed to the Workspace and, when a
        user is behind them, the user), or ``None`` if none is usable."""
        ...


class RegisteredEmbeddingModels:
    """The Workspace's own embedding model, one of its Workspace Models (ADR 0005). A
    Workspace without one has no embedding model: search then matches text only."""

    def __init__(
        self, providers: ProviderService, roles: RoleService, engine: sa.Engine | None = None
    ) -> None:
        self._providers = providers
        self._roles = roles
        self._engine = engine

    def current(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> EmbeddingModel | None:
        if self._engine is None:
            return None
        chosen = workspace_embedding_model_id(self._engine, workspace_id)
        found = [
            (provider, model)
            for provider in self._providers.list_providers()
            for model in provider.models
            if model.id == chosen and "embedding" in model.roles and model.test_ok
        ]
        if not found:
            return None
        provider, model = found[0]
        gateway = self._roles.metered_gateway(
            model.id, "embedding", workspace_id=workspace_id, user_id=user_id
        )
        return EmbeddingModel(model.id, provider.is_internal, gateway)


@dataclass(frozen=True)
class Passage:
    """A passage that matched, with the citation: the document and its section."""

    file_id: uuid.UUID
    document: str
    section: str
    text: str
    score: float
    source_system_id: uuid.UUID


def _unless_other_api_error(exc: Exception) -> None:
    """An exhausted token budget only costs the semantic ranking (full-text still works);
    any other API error is a real failure."""
    if isinstance(exc, ApiError) and exc.code != "token_budget_exhausted":
        raise exc


class _Superseded(Exception):
    """The file was replaced (or deleted) while its index job ran."""


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class DocumentSearchService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        ai: DocumentAiPolicy,
        embeddings: EmbeddingModels,
        jobs: JobRunner,
        storage: FileStorage,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._storage = storage
        self._workspaces = workspaces
        self._ai = ai
        self._embeddings = embeddings
        self._jobs = JobService(engine, runner=jobs, clock=clock)

    def _embedder(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> EmbeddingModel | None:
        """The model to embed this Workspace's documents (and queries) with, or ``None``:
        the data level excludes documents, no embedding model is registered, or the
        Workspace is internal-only and the model's provider is external."""
        settings = self._ai.document_settings(workspace_id)
        if not settings.includes_documents:
            return None
        model = self._embeddings.current(workspace_id, user_id)
        if model is None or (settings.internal_only and not model.is_internal):
            return None
        return model

    # -- indexing ----------------------------------------------------------------------

    def queue_index(self, workspace_id: uuid.UUID, file_id: uuid.UUID) -> None:
        """Queue the ``index_document`` job for a file whose bytes were just stored (the
        caller checked the right to change the file). The job is dropped if the file has
        been replaced by then: the newer bytes have their own job."""
        with Session(self._engine) as db:
            key = db.scalar(
                sa.select(WorkspaceFileRecord.storage_key).where(
                    WorkspaceFileRecord.id == file_id,
                    WorkspaceFileRecord.workspace_id == workspace_id,
                )
            )
        if key is None:
            return
        try:
            self._jobs.submit(
                workspace_id,
                INDEX_JOB,
                {"workspace_id": str(workspace_id), "file_id": str(file_id), "key": key},
                title="Index document",
                created_by=None,  # a per-file status shows the outcome; no notification per file
            )
        except Exception as exc:  # the upload stands; the file must not wait for a job
            logger.exception("queueing the indexing of document %s failed", file_id)
            self._set_index_status(
                file_id,
                key,
                "failed",
                f"Indexing could not be queued: {exc}"[:INDEX_ERROR_MAX_LENGTH],
            )

    def run_index(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``index_document`` job handler: extract the text, then chunk, embed and
        store the passages, reporting the file's ``index_status`` at each step. A failure
        is kept on the file (``index_error``) and fails the job; other files are unaffected."""
        workspace_id, file_id = uuid.UUID(params["workspace_id"]), uuid.UUID(params["file_id"])
        with Session(self._engine) as db:
            record = db.get(WorkspaceFileRecord, file_id)
            if record is None or record.storage_key != params["key"]:
                return  # deleted or replaced since this job was queued
            name = record.path
        key = params["key"]

        def step(status: str) -> None:
            if not self._set_index_status(file_id, key, status):
                raise _Superseded

        try:
            step("extracting")
            ctx.raise_if_cancelled()
            data = self._storage.get(key)
            text, text_status = extract_text(detect(name, data), data)
            with Session(self._engine) as db, db.begin():
                updated = db.execute(
                    sa.update(WorkspaceFileRecord)
                    .where(
                        WorkspaceFileRecord.id == file_id, WorkspaceFileRecord.storage_key == key
                    )
                    .values(extracted_text=text, text_status=text_status)
                )
            if updated.rowcount == 0:
                raise _Superseded
            ctx.raise_if_cancelled()
            self.index_file(
                workspace_id,
                file_id,
                on_stage=step,
                key=key,
                before_embedding=ctx.raise_if_cancelled,
            )
            step("indexed")
        except _Superseded:
            return  # the file was replaced meanwhile: the newer bytes have their own job
        except JobCancelledError:
            self._set_index_status(file_id, key, "failed", "Indexing was cancelled.")
            raise
        except Exception as exc:
            self._set_index_status(
                file_id, key, "failed", (str(exc) or type(exc).__name__)[:INDEX_ERROR_MAX_LENGTH]
            )
            raise

    def abandon_index(self, db: Session, params: Mapping[str, Any], reason: str) -> None:
        """The jobs module's hook for an ``index_document`` job that ended without its
        handler's own cleanup (cancelled while queued, or its worker lost): the file is
        ``failed`` instead of waiting forever."""
        db.execute(
            sa.update(WorkspaceFileRecord)
            .where(
                WorkspaceFileRecord.id == uuid.UUID(params["file_id"]),
                WorkspaceFileRecord.storage_key == params["key"],
                WorkspaceFileRecord.index_status.not_in(("indexed", "failed")),
            )
            .values(index_status="failed", index_error=reason[:INDEX_ERROR_MAX_LENGTH])
        )

    def _set_index_status(
        self, file_id: uuid.UUID, key: str, status: str, error: str | None = None
    ) -> bool:
        """Set the status of the file as long as it still holds the bytes at ``key``;
        whether it did."""
        with Session(self._engine) as db, db.begin():
            result = db.execute(
                sa.update(WorkspaceFileRecord)
                .where(WorkspaceFileRecord.id == file_id, WorkspaceFileRecord.storage_key == key)
                .values(index_status=status, index_error=error)
            )
        return result.rowcount > 0

    def index_file(
        self,
        workspace_id: uuid.UUID,
        file_id: uuid.UUID,
        *,
        on_stage: Callable[[str], None] | None = None,
        key: str | None = None,
        before_embedding: Callable[[], None] | None = None,
    ) -> int:
        """(Re)build the passages of one file; the number of passages. Full-text always,
        embeddings only when ``_embedder`` allows. A provider failure leaves the file
        full-text only (a re-index retries). ``on_stage`` hears ``chunking`` and, when
        the passages are embedded, ``embedding``. With ``key``, the passages are stored only
        while the file still holds the bytes at that key (else ``_Superseded``)."""
        stage = on_stage or (lambda _: None)
        with Session(self._engine) as db:
            record = db.get(WorkspaceFileRecord, file_id)
            text = record.extracted_text if record and record.workspace_id == workspace_id else None
        stage("chunking")
        chunks = chunk(text) if text else []

        vectors: list[list[float]] | None = None
        model = self._embedder(workspace_id) if chunks else None
        if before_embedding is not None:
            before_embedding()
        if model is not None:
            stage("embedding")
            try:
                vectors = []
                for start in range(0, len(chunks), EMBED_BATCH):
                    batch = [c.text for c in chunks[start : start + EMBED_BATCH]]
                    vectors.extend(model.gateway.embed(batch))
            except (LlmError, ApiError) as exc:
                _unless_other_api_error(exc)
                logger.warning("embedding failed, indexing full-text only: %s", exc)
                vectors = None

        with Session(self._engine) as db, db.begin():
            if key is not None and not db.scalar(
                sa.select(WorkspaceFileRecord.id)
                .where(WorkspaceFileRecord.id == file_id, WorkspaceFileRecord.storage_key == key)
                .with_for_update()
            ):
                raise _Superseded
            db.execute(sa.delete(DocumentChunkRecord).where(DocumentChunkRecord.file_id == file_id))
            for ordinal, piece in enumerate(chunks):
                row = DocumentChunkRecord(
                    id=uuid.uuid4(),
                    file_id=file_id,
                    workspace_id=workspace_id,
                    ordinal=ordinal,
                    section=piece.section,
                    content=piece.text,
                    search_text=normalise(piece.text),
                )
                db.add(row)
                if model is not None and vectors is not None and ordinal < len(vectors):
                    db.flush()
                    db.execute(
                        sa.text(
                            "UPDATE document_chunks SET embedding = CAST(:v AS vector), "
                            "embedding_model_id = :m, embedding_dimension = :d WHERE id = :id"
                        ),
                        {
                            "v": _vector_literal(vectors[ordinal]),
                            "m": model.id,
                            "d": len(vectors[ordinal]),
                            "id": row.id,
                        },
                    )
        return len(chunks)

    def start_reindex(self, user: User, workspace_id: uuid.UUID) -> Job:
        """Queue a ``reindex_documents`` job: every document of the Workspace is indexed
        again with the current embedding model and policy (owners and editors). Run it
        after the embedding model or its dimension changes."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        return self._jobs.submit(
            workspace_id,
            REINDEX_JOB,
            {"workspace_id": str(workspace_id)},
            title="Re-index documents",
            created_by=user.id,
        )

    def run_reindex(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``reindex_documents`` job handler."""
        workspace_id = uuid.UUID(params["workspace_id"])
        with Session(self._engine) as db:
            file_ids = list(
                db.scalars(
                    sa.select(WorkspaceFileRecord.id).where(
                        WorkspaceFileRecord.workspace_id == workspace_id,
                        WorkspaceFileRecord.extracted_text.is_not(None),
                    )
                )
            )
        for done, file_id in enumerate(file_ids, start=1):
            ctx.raise_if_cancelled()
            count = self.index_file(workspace_id, file_id)
            ctx.log(f"Indexed {count} passages of document {done} of {len(file_ids)}.")
            ctx.progress(done * 100 // len(file_ids))

    # -- searching ---------------------------------------------------------------------

    def search(
        self,
        user: User,
        workspace_id: uuid.UUID,
        query: str,
        *,
        system_id: uuid.UUID | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[Passage]:
        """The passages that best match ``query``, any member, best first. Full-text and,
        where the passages were embedded, vector rankings are fused."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        terms = words(query)
        if not terms:
            return []
        scope = " AND f.owner_id = :system_id" if system_id else ""
        params: dict[str, Any] = {"w": workspace_id, "system_id": system_id}
        with Session(self._engine) as db:
            # A soft-deleted Source System's documents are hidden until it is purged.
            hidden = hidden_source_system_ids(db, workspace_id)
            if hidden:
                scope += " AND NOT (f.owner_kind = 'source_system' AND f.owner_id = ANY(:hidden))"
                params["hidden"] = hidden
            ranked: list[list[uuid.UUID]] = [
                list(
                    db.scalars(
                        sa.text(
                            "SELECT c.id FROM document_chunks c "
                            "JOIN workspace_files f ON f.id = c.file_id, "
                            "to_tsquery('simple', :q) q "
                            "WHERE c.workspace_id = :w AND c.search_vector @@ q" + scope + " "
                            "ORDER BY ts_rank_cd(c.search_vector, q) DESC, c.id "
                            f"LIMIT {CANDIDATES}"
                        ),
                        {**params, "q": " | ".join(f"'{term}'" for term in terms)},
                    )
                )
            ]
            vector_ranking = self._vector_ranking(db, workspace_id, user.id, query, scope, params)
            if vector_ranking:
                ranked.append(vector_ranking)

            scores: dict[uuid.UUID, float] = {}
            for ranking in ranked:
                for position, chunk_id in enumerate(ranking):
                    scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + position + 1)
            best = sorted(scores, key=lambda cid: (-scores[cid], str(cid)))[:limit]
            rows = {
                row.id: row
                for row in db.execute(
                    sa.select(
                        DocumentChunkRecord.id,
                        DocumentChunkRecord.section,
                        DocumentChunkRecord.content,
                        WorkspaceFileRecord.id.label("file_id"),
                        WorkspaceFileRecord.path,
                        WorkspaceFileRecord.owner_id,
                    )
                    .join(
                        WorkspaceFileRecord, WorkspaceFileRecord.id == DocumentChunkRecord.file_id
                    )
                    .where(DocumentChunkRecord.id.in_(best))
                )
            }
        return [
            Passage(
                file_id=rows[cid].file_id,
                document=rows[cid].path,
                section=rows[cid].section,
                text=rows[cid].content,
                score=scores[cid],
                source_system_id=rows[cid].owner_id,
            )
            for cid in best
        ]

    def _vector_ranking(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        query: str,
        scope: str,
        params: dict[str, Any],
    ) -> list[uuid.UUID]:
        model = self._embedder(workspace_id, user_id)
        if model is None:
            return []
        try:
            [vector] = model.gateway.embed([query])
        except (LlmError, ApiError) as exc:
            _unless_other_api_error(exc)
            logger.warning("query embedding failed, searching full-text only: %s", exc)
            return []
        return list(
            db.scalars(
                sa.text(
                    "WITH candidates AS MATERIALIZED ("
                    "SELECT c.id, c.embedding FROM document_chunks c "
                    "JOIN workspace_files f ON f.id = c.file_id "
                    "WHERE c.workspace_id = :w AND c.embedding_model_id = :m "
                    "AND c.embedding_dimension = :d" + scope + ") "
                    "SELECT id FROM candidates ORDER BY embedding <=> CAST(:v AS vector), id "
                    f"LIMIT {CANDIDATES}"
                ),
                {**params, "m": model.id, "d": len(vector), "v": _vector_literal(vector)},
            )
        )
