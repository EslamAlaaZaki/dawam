"""Searching uploaded documents: full-text, plus vectors when the Workspace allows them.

Every document with extracted text is cut into passages (``document_chunks``). Each
passage is indexed with Postgres full-text search after Arabic normalisation (the query
gets the same treatment). It is also embedded into pgvector, but only when the
Workspace's data-sharing level includes documents and the embedding model passes the
Workspace's internal-only check (spec §6.17, §6.18); otherwise that Workspace is
searched with full-text only and nothing is sent to a model. Search fuses both rankings
(reciprocal rank fusion) and returns cited passages: the document and its section.

Which data level and internal-only setting a Workspace has is not this module's to know:
it asks a ``DocumentAiPolicy``, and which embedding and vision models are registered it asks
an ``EmbeddingModels`` and a ``VisionModels``; the composition root chooses them. The same
rule covers scanned pages: their images go to the Workspace's vision model only when the
policy would let the document's text reach that provider (an internal one always may);
otherwise, or when the vision model fails, the self-hosted OCR engine reads them and the
file's ``index_note`` says so.

When a Workspace's embedding model changes, its documents are embedded again (ADR 0006,
``start_reembed``): search matches text only until the ``reindex_documents`` job switches
to the new vectors, in one transaction, so results never mix two models.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobCancelledError, JobContext, JobRunner, JobService
from dawam.modules.llm import (
    LlmError,
    MeteredGateway,
    ProviderService,
    RoleService,
    workspace_chunking_strategy,
    workspace_embedding_model_id,
    workspace_model_id,
)
from dawam.modules.sources import hidden_source_system_ids
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.storage import FileStorage

from .internal.arabic import language, stemmed, stems
from .internal.chunking import Chunk
from .internal.content import (
    ALLOWED_TYPES,
    EXTRACTED_TEXT_MAX_CHARS,
    IMAGE_TYPES,
    detect,
    extract_text,
    is_image,
)
from .internal.context import context_header
from .internal.ocr import OcrEngine, VisionOcr, describe_image
from .internal.parents import chunk_with_parents
from .internal.parsing import DocumentParser
from .internal.query_rewrite import query_variants
from .internal.rerank import NoRerankModels, RerankModels
from .tables import DocumentChunkRecord, EmbeddingRebuildRecord, WorkspaceFileRecord

logger = logging.getLogger(__name__)

REINDEX_JOB = "reindex_documents"
INDEX_JOB = "index_document"
INDEX_ERROR_MAX_LENGTH = 1000
EMBED_BATCH = 32
CANDIDATES = 20
MAX_POOL_GROWTH = 8  # how far the pool may grow to find ``limit`` distinct parents
"""How many passages each ranking contributes before they are fused."""
RRF_K = 60
DEFAULT_LIMIT = 10
RERANK_TOP_N = 30
"""How many of the fused top passages the rerank model rescores."""
RERANK_DOC_CHARS = 4000


@dataclass(frozen=True)
class SearchTuning:
    """How rankings are fused and trimmed. Code constants for now; they become admin
    settings with the Document AI settings (24.1)."""

    fts_weight: float = 1.0
    vector_weight: float = 1.0
    rrf_k: int = RRF_K
    fts_candidates: int = CANDIDATES
    vector_candidates: int = CANDIDATES
    mmr_lambda: float = 0.7
    """1.0 is pure relevance; lower values trade it for variety."""
    same_document_similarity: float = 0.3
    """How alike two passages of one document (other sections) count for MMR."""
    min_text_rank: float = 0.01
    """The ``ts_rank_cd`` a full-text match needs to count as relevant."""
    min_vector_similarity: float = 0.3
    """The cosine similarity a vector match needs to count as relevant. A passage is returned
    only if it clears the floor of at least one of its rankings; below both, nothing is."""


DEFAULT_TUNING = SearchTuning()


@dataclass(frozen=True)
class FileIndexStatus:
    """One row of the index status page."""

    file_id: uuid.UUID
    name: str
    index_status: str
    index_error: str | None
    index_note: str | None
    chunk_count: int
    embedded_count: int
    stale_count: int
    updated_at: datetime


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
    """The Workspace's own embedding model, one of its Workspace Models (ADR 0006). A
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
class VisionModel:
    is_internal: bool
    gateway: MeteredGateway
    """Metered: the token budgets are checked before every call, and internal-only applies."""


class VisionModels(Protocol):
    def current(self, workspace_id: uuid.UUID) -> VisionModel | None:
        """The Workspace's vision model, or ``None`` if it has none usable."""
        ...


class NoVisionModels:
    """No Workspace has a vision model: scanned pages go to the self-hosted OCR engine."""

    def current(self, workspace_id: uuid.UUID) -> VisionModel | None:
        return None


class RegisteredVisionModels:
    """The Workspace's own vision model, one of its Workspace Models (optional)."""

    def __init__(self, providers: ProviderService, roles: RoleService, engine: sa.Engine) -> None:
        self._providers = providers
        self._roles = roles
        self._engine = engine

    def current(self, workspace_id: uuid.UUID) -> VisionModel | None:
        chosen = workspace_model_id(self._engine, workspace_id, "vision")
        if chosen is None:
            return None
        found = [
            (provider, model)
            for provider in self._providers.list_providers()
            for model in provider.models
            if model.id == chosen and "vision" in model.roles and model.test_ok
        ]
        if not found:
            return None
        provider, model = found[0]
        gateway = self._roles.metered_gateway(model.id, "vision", workspace_id=workspace_id)
        return VisionModel(provider.is_internal, gateway)


VISION_REFUSED = (
    "The Workspace's data-sharing level does not let documents reach its vision model's "
    "provider, so {fallback}."
)
VISION_FAILED = "The vision model could not read the scanned pages ({reason}), so {fallback}."
SELF_HOSTED = "the self-hosted OCR engine read the scanned pages"
NO_FALLBACK = "the scanned pages were not read (the self-hosted OCR engine is off)"


class _Unread:
    """No engine may read the scans: they keep the text they have (none, for a scan)."""

    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        return ""


class _VisionThenSelfHosted:
    """The vision model reads the scans; if it fails, the self-hosted engine does (if any),
    and ``note`` hears why."""

    def __init__(
        self, vision: OcrEngine, fallback: OcrEngine | None, note: Callable[[str], None]
    ) -> None:
        self._vision = vision
        self._fallback = fallback
        self._note = note

    def recognise(self, data: bytes, pages: Sequence[int]) -> str:
        try:
            return self._vision.recognise(data, pages)
        except Exception as exc:
            logger.warning("the vision model could not read a scan", exc_info=True)
            reason = exc.message if isinstance(exc, LlmError | ApiError) else type(exc).__name__
            fallback = SELF_HOSTED if self._fallback else NO_FALLBACK
            self._note(VISION_FAILED.format(reason=reason, fallback=fallback))
            if self._fallback is None:
                raise
            return self._fallback.recognise(data, pages)


IMAGE_NOT_DESCRIBED = (
    "The image was not described: the Workspace has no vision model that its data-sharing "
    "level lets the image reach."
)


@dataclass(frozen=True)
class LightModel:
    is_internal: bool
    gateway: MeteredGateway


class LightModels(Protocol):
    def current(self, workspace_id: uuid.UUID) -> LightModel | None:
        """The Workspace's light model, or ``None``: it has none (no fallback to the agent)."""
        ...


class NoLightModels:
    def current(self, workspace_id: uuid.UUID) -> LightModel | None:
        return None


class RegisteredLightModels:
    """The Workspace's default light model, one of its Workspace Models (optional)."""

    def __init__(self, providers: ProviderService, roles: RoleService, engine: sa.Engine) -> None:
        self._providers = providers
        self._roles = roles
        self._engine = engine

    def current(self, workspace_id: uuid.UUID) -> LightModel | None:
        chosen = workspace_model_id(self._engine, workspace_id, "light")
        if chosen is None:
            return None
        for provider in self._providers.list_providers():
            for model in provider.models:
                if model.id == chosen and "light" in model.roles:
                    gateway = self._roles.metered_gateway(
                        model.id, "light", workspace_id=workspace_id
                    )
                    return LightModel(provider.is_internal, gateway)
        return None


IMAGE_VISION_FAILED = "The vision model could not describe the image ({reason})."


@dataclass(frozen=True)
class ImageDescription:
    file_id: uuid.UUID
    name: str
    description: str
    """Markdown the vision model wrote about the image: untrusted text."""


@dataclass(frozen=True)
class Passage:
    """A passage that matched, with the citation: the document and its section."""

    file_id: uuid.UUID
    document: str
    section: str
    text: str
    score: float
    source_system_id: uuid.UUID
    document_type: str | None = None
    language: str | None = None
    page: int | None = None
    sheet: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PassageFilters:
    """Narrow a search by what is stored per passage; every field set must match."""

    document_type: str | None = None
    language: str | None = None
    page: int | None = None
    sheet: str | None = None
    tag: str | None = None


def _unless_other_api_error(exc: Exception) -> None:
    """An exhausted token budget only costs the semantic ranking (full-text still works);
    any other API error is a real failure."""
    if isinstance(exc, ApiError) and exc.code != "token_budget_exhausted":
        raise exc


class _Superseded(Exception):
    """The file was replaced (or deleted) while its index job ran."""


_TYPE_BY_MIME = {t.mime: t.name for t in ALLOWED_TYPES}


def _tags(path: str) -> list[str]:
    """The labels of a document: the lower-cased folders of its path."""
    return list(dict.fromkeys(p.casefold() for p in path.split("/")[:-1] if p))


HNSW_MAX_DIMENSION = 2000
"""pgvector's limit for an HNSW index on ``vector``; wider embeddings are searched by scan."""


def ensure_vector_index(engine: sa.Engine, model_id: uuid.UUID, dimension: int) -> bool:
    """Create the partial HNSW index of one embedding model (and dimension) if it is missing;
    whether the model has one. The column has no fixed dimension, so the index is on a cast
    to ``vector(dimension)`` and is limited to the model's rows; vector queries use the same
    cast and predicate (``_vector_ranking``) so the planner picks it."""
    if not 0 < dimension <= HNSW_MAX_DIMENSION:
        return False
    name = f"ix_document_chunks_hnsw_{model_id.hex}_{dimension}"
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:n))"), {"n": name})
        conn.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS {name} ON document_chunks "
                f"USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) "
                f"WHERE embedding_model_id = '{model_id}' AND embedding_dimension = {dimension}"
            )
        )
    return True


def _embed_all(gateway: Any, texts: list[str]) -> list[list[float]]:
    """Vectors of ``texts`` through the embedding model's gateway, in batches."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(gateway.embed(texts[start : start + EMBED_BATCH]))
    return vectors


def _chunks(text: str, counter: Any, strategy: str, embed: Any) -> list[Any]:
    options: dict[str, Any] = {"strategy": strategy, "embed": embed}
    if counter:
        return chunk_with_parents(text, counter, **options)
    return chunk_with_parents(text, **options)


def _match_boosts(query: str, terms: Sequence[str]) -> tuple[str, dict[str, str]]:
    """``ORDER BY`` terms that put exact matches ahead of the plain OR ranking: passages
    holding a table or column name from the query (a word with ``_`` or a digit; the
    parser reads ``cust_stat`` as the phrase ``cust <-> stat``) first, then those holding
    the query's words in order and adjacent. Empty when the query has neither."""
    boosts, params = "", {}
    identifiers = [t for t in terms if "_" in t or any(c.isdigit() for c in t)]
    if identifiers:
        params["ids"] = " | ".join(f"'{t}'" for t in identifiers)
        boosts += "(c.search_vector @@ to_tsquery('simple', :ids)) DESC, "
    if len(terms) > 1:
        params["phrase"] = stemmed(query)
        boosts += "(c.search_vector @@ phraseto_tsquery('simple', :phrase)) DESC, "
    return boosts, params


def _promote_exact(
    db: Session, passages: list[Passage], params: Mapping[str, Any], scope: str
) -> list[Passage]:
    """``passages`` with those holding a query identifier moved to the front (stable), so
    fused vector hits or a reranker cannot bury an exact name match."""
    if "ids" not in params or not passages:
        return passages
    exact = {
        (row.file_id, row.text)
        for row in db.execute(
            sa.text(
                "SELECT c.file_id, COALESCE(c.parent_content, c.content) AS text "
                "FROM document_chunks c JOIN workspace_files f ON f.id = c.file_id "
                "WHERE c.workspace_id = :w AND c.search_vector @@ to_tsquery('simple', :ids)"
                + scope
            ),
            params,
        )
    }
    return [p for p in passages if (p.file_id, p.text) in exact] + [
        p for p in passages if (p.file_id, p.text) not in exact
    ]


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
        parser: Callable[[], DocumentParser | None] = lambda: None,
        ocr: Callable[[], OcrEngine | None] = lambda: None,
        vision: VisionModels | None = None,
        light: LightModels | None = None,
        rerank: RerankModels | None = None,
    ) -> None:
        self._rerank_models = rerank or NoRerankModels()
        self._light = light or NoLightModels()
        self._parser = parser
        self._ocr = ocr
        self._vision = vision or NoVisionModels()
        self._engine = engine
        self._clock = clock
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

    def _vision_model(self, workspace_id: uuid.UUID) -> VisionModel | None:
        """The Workspace's vision model if it may be sent a document's image, else ``None``:
        the rule of ``_ocr_engine`` (an internal model always may)."""
        model = self._vision.current(workspace_id)
        return model if model is not None and self._may_send(workspace_id, model) else None

    def _light_model(self, workspace_id: uuid.UUID) -> LightModel | None:
        """The light model that writes passage context, or ``None`` (the feature is off): the
        Workspace has none, or the data level / internal-only keeps documents from it."""
        model = self._light.current(workspace_id)
        if model is None:
            return None
        settings = self._ai.document_settings(workspace_id)
        if settings.internal_only and not model.is_internal:
            return None
        if not (model.is_internal or settings.includes_documents):
            return None
        return model

    def _headers(
        self, workspace_id: uuid.UUID, name: str, text: str, chunks: Sequence[Chunk]
    ) -> list[str]:
        """The context sentence of each passage (empty where there is none). All empty when
        the feature is off. A failing call costs only the headers (the file still indexes)."""
        model = self._light_model(workspace_id) if chunks else None
        if model is None:
            return [""] * len(chunks)
        headers: list[str] = []
        failing = False
        for piece in chunks:
            header = ""
            if not failing:
                try:
                    header = context_header(model.gateway, name, piece.section, text, piece.text)
                except (LlmError, ApiError) as exc:
                    logger.warning("passage context failed, indexing without it: %s", exc)
                    failing = True  # likely the same for the rest: stop spending calls
            headers.append(header)
        return headers

    def _may_send(self, workspace_id: uuid.UUID, model: VisionModel) -> bool:
        settings = self._ai.document_settings(workspace_id)
        return model.is_internal or (settings.includes_documents and not settings.internal_only)

    def _describe(
        self, workspace_id: uuid.UUID, name: str, mime: str, data: bytes
    ) -> tuple[str | None, str | None]:
        """``(text, note)`` for an image: the searchable description (headed with the file's
        name) or, when it was not described, the note saying why."""
        model = self._vision_model(workspace_id)
        if model is None:
            return None, IMAGE_NOT_DESCRIBED
        try:
            description = describe_image(model.gateway, mime, data)
        except (LlmError, ApiError) as exc:
            logger.warning("the vision model could not describe an image", exc_info=True)
            return None, IMAGE_VISION_FAILED.format(reason=exc.message)
        if not description:
            return None, IMAGE_VISION_FAILED.format(reason="it said nothing")
        return f"# Description of {name}\n\n{description}"[:EXTRACTED_TEXT_MAX_CHARS], None

    def describe_image(
        self, user: User, workspace_id: uuid.UUID, file_id: uuid.UUID
    ) -> ImageDescription:
        """What an image shows, for the assistant's ``describe_image`` tool (any member): the
        description made on indexing, or else a fresh look by the vision model when the
        Workspace's policy lets the image reach it. Read-only: nothing is stored."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = db.get(WorkspaceFileRecord, file_id)
            if record is None or record.workspace_id != workspace_id:
                raise ApiError(404, "not_found", "File not found.")
            name, mime, key, stored = (
                record.path,
                record.mime,
                record.storage_key,
                record.extracted_text,
            )
        if mime not in {t.mime for t in IMAGE_TYPES}:
            raise ApiError(422, "not_an_image", "The file is not an image.")
        if stored:
            return ImageDescription(file_id, name, stored)
        try:
            data = self._storage.get(key)
        except KeyError:
            raise ApiError(404, "not_found", "File not found.") from None
        text, note = self._describe(workspace_id, name, mime, data)
        if text is None:
            raise ApiError(422, "no_vision", note or IMAGE_NOT_DESCRIBED)
        return ImageDescription(file_id, name, text)

    def _ocr_engine(
        self, workspace_id: uuid.UUID, note: Callable[[str], None]
    ) -> tuple[OcrEngine | None, str | None]:
        """The engine for this Workspace's scanned pages, and the note to keep if it reads
        any: the vision model when the Workspace has one and may send it the document (an
        internal model always may: the policy rule of ``_embedder``), otherwise the
        self-hosted engine; when that is off too, nothing reads them, and the note says so."""
        fallback = self._ocr()
        model = self._vision.current(workspace_id)
        if model is None:
            return fallback, None
        if self._may_send(workspace_id, model):
            return _VisionThenSelfHosted(VisionOcr(model.gateway), fallback, note), None
        if fallback is None:
            return _Unread(), VISION_REFUSED.format(fallback=NO_FALLBACK)
        return fallback, VISION_REFUSED.format(fallback=SELF_HOSTED)

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
            notes: list[str] = []
            ocr, refused = self._ocr_engine(workspace_id, notes.append)

            def reading_scan() -> None:
                step("ocr")
                ctx.raise_if_cancelled()
                if refused:
                    notes.append(refused)

            file_type = detect(name, data)
            if is_image(file_type):
                step("ocr")
                ctx.raise_if_cancelled()
                text, note = self._describe(workspace_id, name, file_type.mime, data)
                text_status = "extracted" if text else "none"
                if note:
                    notes.append(note)
            else:
                text, text_status = extract_text(
                    file_type, data, self._parser(), ocr, on_ocr=reading_scan
                )
            with Session(self._engine) as db, db.begin():
                updated = db.execute(
                    sa.update(WorkspaceFileRecord)
                    .where(
                        WorkspaceFileRecord.id == file_id, WorkspaceFileRecord.storage_key == key
                    )
                    .values(
                        extracted_text=text,
                        text_status=text_status,
                        index_note=" ".join(notes) or None,
                    )
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
            kind = _TYPE_BY_MIME.get(record.mime) if record else None
            folders = _tags(record.path) if record else []
            name = record.path if record else ""
        stage("chunking")
        model = self._embedder(workspace_id) if text and text.strip() else None
        # Passage size is measured in the embedding model's own tokens, counted locally
        # (never a provider call); without a local tokenizer the estimate is used.
        counter = model.gateway.local_token_counter() if model else None
        strategy = workspace_chunking_strategy(self._engine, workspace_id)
        embed = (lambda texts: _embed_all(model.gateway, texts)) if model else None
        try:
            chunks = _chunks(text or "", counter, strategy, embed)
        except (LlmError, ApiError) as exc:  # semantic chunking embeds sentences
            _unless_other_api_error(exc)
            logger.warning("semantic chunking failed, using recursive: %s", exc)
            chunks = _chunks(text or "", counter, "recursive", None)
        if not chunks:
            model = None
        headers = self._headers(workspace_id, name, text or "", [c.chunk for c in chunks])
        indexed = [
            f"{h}\n{c.chunk.text}" if h else c.chunk.text
            for h, c in zip(headers, chunks, strict=True)
        ]

        vectors: list[list[float]] | None = None
        if before_embedding is not None:
            before_embedding()
        if model is not None:
            stage("embedding")
            try:
                vectors = []
                for start in range(0, len(chunks), EMBED_BATCH):
                    batch = indexed[start : start + EMBED_BATCH]
                    vectors.extend(model.gateway.embed(batch))
            except (LlmError, ApiError) as exc:
                _unless_other_api_error(exc)
                logger.warning("embedding failed, indexing full-text only: %s", exc)
                vectors = None

        if model is not None and vectors:
            try:
                ensure_vector_index(self._engine, model.id, len(vectors[0]))
            except sa.exc.SQLAlchemyError:  # search still works, by scan
                logger.warning("creating the vector index failed", exc_info=True)

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
                    section=piece.chunk.section,
                    content=piece.chunk.text,
                    context_header=headers[ordinal] or None,
                    search_text=stemmed(indexed[ordinal]),
                    parent_ordinal=piece.parent,
                    parent_content=piece.parent_text,
                    document_type=kind,
                    language=language(piece.chunk.text),
                    page=piece.chunk.page,
                    sheet=piece.chunk.section if kind == "xlsx" else None,
                    tags=folders,
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
        after the embedding model or its dimension changes. A re-embed left unfinished
        (its job lost) is taken over: this job switches to the new vectors instead."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        with Session(self._engine) as db, db.begin():
            params = {"workspace_id": str(workspace_id)}
            if db.get(EmbeddingRebuildRecord, workspace_id, with_for_update=True) is not None:
                params["rebuild_id"] = str(self._mark_rebuild(db, workspace_id))
            return self._jobs.submit(
                workspace_id,
                REINDEX_JOB,
                params,
                title="Re-index documents",
                created_by=user.id,
                db=db,
            )

    def index_overview(self, user: User, workspace_id: uuid.UUID) -> list[FileIndexStatus]:
        """Where indexing stands for every file of the Workspace (any member), by name: its
        status, passages, how many are embedded and how many of those were made by a model
        other than the one in use (stale until a re-index)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        model = self._embedder(workspace_id)
        current = model.id if model else None
        chunks = (
            sa.select(
                DocumentChunkRecord.file_id.label("file_id"),
                sa.func.count().label("total"),
                sa.func.count(DocumentChunkRecord.embedding).label("embedded"),
                sa.func.count()
                .filter(
                    DocumentChunkRecord.embedding.is_not(None),
                    DocumentChunkRecord.embedding_model_id.is_distinct_from(current),
                )
                .label("stale"),
            )
            .where(DocumentChunkRecord.workspace_id == workspace_id)
            .group_by(DocumentChunkRecord.file_id)
            .subquery()
        )
        files = WorkspaceFileRecord
        with Session(self._engine) as db:
            hidden = hidden_source_system_ids(db, workspace_id)
            query = (
                sa.select(files, chunks.c.total, chunks.c.embedded, chunks.c.stale)
                .outerjoin(chunks, chunks.c.file_id == files.id)
                .where(files.workspace_id == workspace_id)
                .order_by(files.path)
            )
            if hidden:
                query = query.where(
                    sa.not_(
                        sa.and_(files.owner_kind == "source_system", files.owner_id.in_(hidden))
                    )
                )
            return [
                FileIndexStatus(
                    file_id=record.id,
                    name=record.path,
                    index_status=record.index_status,
                    index_error=record.index_error,
                    index_note=record.index_note,
                    chunk_count=total or 0,
                    embedded_count=embedded or 0,
                    stale_count=stale or 0,
                    updated_at=record.updated_at,
                )
                for record, total, embedded, stale in db.execute(query)
            ]

    def reindex_file(self, user: User, workspace_id: uuid.UUID, file_id: uuid.UUID) -> None:
        """Index one file again (owners and editors), as an ``index_document`` job: its status
        goes back to ``queued`` and its error is cleared."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        with Session(self._engine) as db, db.begin():
            result = db.execute(
                sa.update(WorkspaceFileRecord)
                .where(
                    WorkspaceFileRecord.id == file_id,
                    WorkspaceFileRecord.workspace_id == workspace_id,
                )
                .values(index_status="queued", index_error=None)
            )
        if result.rowcount == 0:
            raise ApiError(404, "not_found", "File not found.")
        self.queue_index(workspace_id, file_id)

    def start_reembed(self, db: Session, workspace_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        """The ``EmbeddingModelChangedHook`` (ADR 0006), inside the transaction that changes
        the Workspace's embedding model: from then on search matches text only, and a
        ``reindex_documents`` job builds the new vectors, then switches to them at once. A
        rebuild already running is superseded: only the newest one switches."""
        rebuild_id = self._mark_rebuild(db, workspace_id)
        self._jobs.submit(
            workspace_id,
            REINDEX_JOB,
            {"workspace_id": str(workspace_id), "rebuild_id": str(rebuild_id)},
            title="Re-embed documents",
            created_by=actor_id,
            db=db,
        )

    def _mark_rebuild(self, db: Session, workspace_id: uuid.UUID) -> uuid.UUID:
        rebuild_id = uuid.uuid4()
        db.execute(
            insert(EmbeddingRebuildRecord)
            .values(workspace_id=workspace_id, rebuild_id=rebuild_id, started_at=self._clock())
            .on_conflict_do_update(
                index_elements=[EmbeddingRebuildRecord.workspace_id],
                set_={"rebuild_id": rebuild_id, "started_at": self._clock()},
            )
        )
        return rebuild_id

    def run_reindex(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``reindex_documents`` job handler. With a ``rebuild_id`` (a re-embed), it
        stops once a newer rebuild supersedes it, and however it ends it switches the
        Workspace to the new vectors (``_switch``)."""
        workspace_id = uuid.UUID(params["workspace_id"])
        rebuild = uuid.UUID(params["rebuild_id"]) if "rebuild_id" in params else None
        try:
            self._reindex(workspace_id, rebuild, ctx)
        finally:
            if rebuild is not None:
                with Session(self._engine) as db, db.begin():
                    self._switch(db, workspace_id, rebuild)

    def _reindex(self, workspace_id: uuid.UUID, rebuild: uuid.UUID | None, ctx: JobContext) -> None:
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
            if rebuild is not None and not self._rebuild_current(workspace_id, rebuild):
                ctx.log("A newer change of the embedding model took over; stopping.")
                return
            count = self.index_file(workspace_id, file_id)
            ctx.log(f"Indexed {count} passages of document {done} of {len(file_ids)}.")
            ctx.progress(done * 100 // len(file_ids))

    def _rebuild_current(self, workspace_id: uuid.UUID, rebuild: uuid.UUID) -> bool:
        with Session(self._engine) as db:
            return (
                db.scalar(
                    sa.select(EmbeddingRebuildRecord.rebuild_id).where(
                        EmbeddingRebuildRecord.workspace_id == workspace_id
                    )
                )
                == rebuild
            )

    def _switch(self, db: Session, workspace_id: uuid.UUID, rebuild: uuid.UUID) -> None:
        """End the rebuild ``rebuild`` unless a newer one superseded it: in one transaction,
        drop every vector not made by the current embedding model (a document that failed
        to embed stays text only) and let search use vectors again."""
        ended = db.execute(
            sa.delete(EmbeddingRebuildRecord).where(
                EmbeddingRebuildRecord.workspace_id == workspace_id,
                EmbeddingRebuildRecord.rebuild_id == rebuild,
            )
        )
        if ended.rowcount == 0:
            return
        model = self._embedder(workspace_id)
        db.execute(
            sa.update(DocumentChunkRecord)
            .where(
                DocumentChunkRecord.workspace_id == workspace_id,
                DocumentChunkRecord.embedding_model_id.is_not(None),
                DocumentChunkRecord.embedding_model_id.is_distinct_from(
                    model.id if model else None
                ),
            )
            .values(embedding=None, embedding_model_id=None, embedding_dimension=None)
        )

    def abandon_reindex(self, db: Session, params: Mapping[str, Any], reason: str) -> None:
        """The jobs module's hook for a ``reindex_documents`` job that ended without running
        (cancelled while queued, or its worker lost): a re-embed still switches, so search
        does not stay text only."""
        if "rebuild_id" in params:
            self._switch(db, uuid.UUID(params["workspace_id"]), uuid.UUID(params["rebuild_id"]))

    # -- searching ---------------------------------------------------------------------

    def search(
        self,
        user: User,
        workspace_id: uuid.UUID,
        query: str,
        *,
        system_id: uuid.UUID | None = None,
        limit: int = DEFAULT_LIMIT,
        filters: PassageFilters | None = None,
        tuning: SearchTuning | None = None,
    ) -> list[Passage]:
        """The passages that best match ``query``, any member, best first. Full-text and,
        where the passages were embedded, vector rankings are fused (weighted RRF), passages
        below the relevance floor are dropped and the rest are diversified (MMR)."""
        tune = tuning or DEFAULT_TUNING
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        terms = stems(query)
        if not terms:
            return []
        scope = " AND f.owner_id = :system_id" if system_id else ""
        params: dict[str, Any] = {"w": workspace_id, "system_id": system_id}
        scope += self._filter_scope(filters, params)
        with Session(self._engine) as db:
            # A soft-deleted Source System's documents are hidden until it is purged.
            hidden = hidden_source_system_ids(db, workspace_id)
            if hidden:
                scope += " AND NOT (f.owner_kind = 'source_system' AND f.owner_id = ANY(:hidden))"
                params["hidden"] = hidden
            boosts, boost_params = _match_boosts(query, terms)
            params.update(boost_params)
            queries = [query, *self._variants(workspace_id, query)]
            term_sets = [t for t in (stems(q) for q in queries) if t]
            embedded = self._embed_queries(db, workspace_id, user.id, queries)
            fts = sa.text(
                "SELECT c.id, ts_rank_cd(c.search_vector, q) AS signal FROM document_chunks c "
                "JOIN workspace_files f ON f.id = c.file_id, "
                "to_tsquery('simple', :q) q "
                "WHERE c.workspace_id = :w AND c.search_vector @@ q" + scope + " "
                f"ORDER BY {boosts}ts_rank_cd(c.search_vector, q) DESC, c.id "
                "LIMIT :pool"
            )
            # Several matching children share one parent, so the candidate pool grows
            # until it holds ``limit`` distinct parents or the rankings run dry.
            grow = 1
            while True:
                fts_pool = tune.fts_candidates * grow
                vector_pool = tune.vector_candidates * grow
                # One ranking per query variant (full-text) and per vector, each with the
                # weight, floor and pool size of its kind.
                ranked: list[list[tuple[uuid.UUID, float]]] = []
                weights: list[float] = []
                floors: list[float] = []
                pools: list[int] = []
                for term_set in term_sets:
                    ranked.append(
                        _signals(
                            db.execute(
                                fts,
                                {
                                    **params,
                                    "q": " | ".join(f"'{term}'" for term in term_set),
                                    "pool": fts_pool,
                                },
                            )
                        )
                    )
                    weights.append(tune.fts_weight)
                    floors.append(tune.min_text_rank)
                    pools.append(fts_pool)
                if embedded is not None:
                    model, vectors = embedded
                    for vector in vectors:
                        ranked.append(
                            _signals(
                                db.execute(
                                    sa.text(
                                        vector_ranking_sql(
                                            model.id, len(vector), scope, vector_pool
                                        )
                                    ),
                                    {**params, "v": _vector_literal(vector)},
                                )
                            )
                        )
                        weights.append(tune.vector_weight)
                        floors.append(tune.min_vector_similarity)
                        pools.append(vector_pool)
                scores: dict[uuid.UUID, float] = {}
                relevant: set[uuid.UUID] = set()
                for ranking, weight, floor in zip(ranked, weights, floors, strict=True):
                    for position, (chunk_id, signal) in enumerate(ranking):
                        scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (
                            tune.rrf_k + position + 1
                        )
                        if signal >= floor:
                            relevant.add(chunk_id)
                ordered = sorted(relevant, key=lambda cid: (-scores[cid], str(cid)))
                rows = {
                    row.id: row
                    for row in db.execute(
                        sa.select(
                            DocumentChunkRecord.id,
                            DocumentChunkRecord.section,
                            DocumentChunkRecord.content,
                            DocumentChunkRecord.parent_ordinal,
                            DocumentChunkRecord.parent_content,
                            DocumentChunkRecord.document_type,
                            DocumentChunkRecord.language,
                            DocumentChunkRecord.page,
                            DocumentChunkRecord.sheet,
                            DocumentChunkRecord.tags,
                            WorkspaceFileRecord.id.label("file_id"),
                            WorkspaceFileRecord.path,
                            WorkspaceFileRecord.owner_id,
                        )
                        .join(
                            WorkspaceFileRecord,
                            WorkspaceFileRecord.id == DocumentChunkRecord.file_id,
                        )
                        .where(DocumentChunkRecord.id.in_(ordered))
                    )
                }
                passages = self._parents(ordered, rows, scores)
                # A ranking that ran into the floor has nothing more to give: growing the pool
                # would only add passages below it.
                full = any(
                    len(r) >= size and r[-1][1] >= floor
                    for r, size, floor in zip(ranked, pools, floors, strict=True)
                )
                if len(passages) >= limit or not full or grow >= MAX_POOL_GROWTH:
                    reranked = self._rerank(workspace_id, user.id, query, passages)
                    diverse = self._diversify(reranked[: max(RERANK_TOP_N, limit)], limit, tune)
                    return _promote_exact(db, diverse, params, scope)[:limit]
                grow *= 2

    @staticmethod
    def _diversify(passages: list[Passage], limit: int, tune: SearchTuning) -> list[Passage]:
        """Maximal marginal relevance: pick ``limit`` passages, each the best trade between its
        relevance and how little it repeats what is already picked (same section: fully
        alike; same document, another section: a little)."""
        if len(passages) <= 1 or tune.mmr_lambda >= 1.0:
            return passages[:limit]
        scores = [p.score for p in passages]
        low = min(0.0, min(scores))  # scores are relative to the best; shifted if any is negative
        span = (max(scores) - low) or 1.0
        remaining = list(passages)
        picked: list[Passage] = []

        def similarity(a: Passage, b: Passage) -> float:
            if a.file_id != b.file_id:
                return 0.0
            return 1.0 if a.section == b.section else tune.same_document_similarity

        while remaining and len(picked) < limit:
            best = max(
                remaining,
                key=lambda p: (
                    tune.mmr_lambda * ((p.score - low) / span)
                    - (1 - tune.mmr_lambda) * max((similarity(p, q) for q in picked), default=0.0)
                ),
            )
            picked.append(best)
            remaining.remove(best)
        return picked

    def _rerank(
        self, workspace_id: uuid.UUID, user_id: uuid.UUID, query: str, passages: list[Passage]
    ) -> list[Passage]:
        """Reorder the fused top passages by the Workspace's rerank model, best first, each
        passage's score becoming the model's. Without a rerank model, one the data-sharing
        level (an internal model always may) or internal-only setting keeps from the
        documents, or when the call fails or the
        budget is used up, the fused order stands."""
        settings = self._ai.document_settings(workspace_id)
        model = self._rerank_models.current(workspace_id, user_id)
        if (
            len(passages) < 2
            or model is None
            or not (model.is_internal or settings.includes_documents)
            or (settings.internal_only and not model.is_internal)
        ):
            return passages
        top, rest = passages[:RERANK_TOP_N], passages[RERANK_TOP_N:]
        try:
            scores = model.gateway.rerank(query, [p.text[:RERANK_DOC_CHARS] for p in top])
        except (LlmError, ApiError) as exc:
            _unless_other_api_error(exc)
            logger.warning("reranking failed, keeping the fused order: %s", exc)
            return passages
        if len(scores) != len(top):
            return passages
        reranked = sorted(zip(top, scores, strict=True), key=lambda pair: -pair[1])
        return [replace(p, score=s) for p, s in reranked] + rest

    @staticmethod
    def _filter_scope(filters: PassageFilters | None, params: dict[str, Any]) -> str:
        """The SQL conditions (on chunk ``c``) for ``filters``, binding their values."""
        if filters is None:
            return ""
        scope = ""
        if filters.document_type:
            scope += " AND c.document_type = :f_type"
            params["f_type"] = filters.document_type.casefold()
        if filters.language:
            scope += " AND c.language = :f_language"
            params["f_language"] = filters.language.casefold()
        if filters.page is not None:
            scope += " AND c.page = :f_page"
            params["f_page"] = filters.page
        if filters.sheet:
            scope += " AND c.sheet = :f_sheet"
            params["f_sheet"] = filters.sheet
        if filters.tag:
            scope += " AND :f_tag = ANY(c.tags)"
            params["f_tag"] = filters.tag.casefold()
        return scope

    @staticmethod
    def _parents(
        ordered: list[uuid.UUID], rows: Mapping[uuid.UUID, Any], scores: Mapping[uuid.UUID, float]
    ) -> list[Passage]:
        """Children are matched; a parent is returned once, at its best child's rank."""
        passages: list[Passage] = []
        seen: set[tuple[uuid.UUID, int]] = set()
        for cid in ordered:
            row = rows[cid]
            if row.parent_ordinal is not None:
                if (row.file_id, row.parent_ordinal) in seen:
                    continue
                seen.add((row.file_id, row.parent_ordinal))
            passages.append(
                Passage(
                    file_id=row.file_id,
                    document=row.path,
                    section=row.section,
                    text=row.parent_content or row.content,
                    score=scores[cid],
                    source_system_id=row.owner_id,
                    document_type=row.document_type,
                    language=row.language,
                    page=row.page,
                    sheet=row.sheet,
                    tags=tuple(row.tags),
                )
            )
        return passages

    def _variants(self, workspace_id: uuid.UUID, query: str) -> list[str]:
        """Other phrasings of ``query`` from the light model; none when the feature is off
        (no light model, or the data level / internal-only keeps the query from it) or the
        call fails: the query is then searched as typed."""
        model = self._light_model(workspace_id)
        if model is None:
            return []
        try:
            return query_variants(model.gateway, query)
        except (LlmError, ApiError) as exc:
            _unless_other_api_error(exc)
            logger.warning("query rewriting failed, searching as typed: %s", exc)
            return []

    def _embed_queries(
        self, db: Session, workspace_id: uuid.UUID, user_id: uuid.UUID, queries: list[str]
    ):
        if db.get(EmbeddingRebuildRecord, workspace_id) is not None:
            return None  # the vectors are being rebuilt for a new model: text only meanwhile
        model = self._embedder(workspace_id, user_id)
        if model is None:
            return None
        try:
            vectors = model.gateway.embed(queries)
        except (LlmError, ApiError) as exc:
            _unless_other_api_error(exc)
            logger.warning("query embedding failed, searching full-text only: %s", exc)
            return None
        return model, vectors


def _signals(rows: Any) -> list[tuple[uuid.UUID, float]]:
    """``(chunk id, absolute relevance signal)`` of a ranking's rows, best first."""
    return [(row[0], float(row[1] or 0.0)) for row in rows]


def vector_ranking_sql(
    model_id: uuid.UUID, dimension: int, scope: str = "", pool: int = CANDIDATES
) -> str:
    """The vector query. Its cast and predicate are those of the model's HNSW index (see
    ``ensure_vector_index``); the model id and dimension are literals so the planner can prove
    the index predicate even under a generic plan. The UUID type and the int are the injection
    guard for those literals. Binds: ``:w`` (workspace), ``:v`` (query vector), plus the scope's."""
    cast = f"vector({dimension})" if 0 < dimension <= HNSW_MAX_DIMENSION else "vector"
    column = f"(c.embedding::{cast})" if cast != "vector" else "c.embedding"
    return (
        f"SELECT c.id, 1 - ({column} <=> CAST(:v AS {cast})) AS signal "
        "FROM document_chunks c "
        "JOIN workspace_files f ON f.id = c.file_id "
        f"WHERE c.workspace_id = :w AND c.embedding_model_id = '{model_id}' "
        f"AND c.embedding_dimension = {int(dimension)}{scope} "
        f"ORDER BY {column} <=> CAST(:v AS {cast}) "
        f"LIMIT {int(pool)}"
    )
