"""Document AI settings (24.1): the knobs of the RAG pipeline (chunking, indexing,
retrieval, reranking), in one place instead of code constants.

An admin sets the installation's values and, for each numeric knob owners may override,
the bounds they override within. A Workspace's owners override the retrieval and chunking
knobs (``Knob.owner``); models, providers, data sharing, budgets and every other knob stay
admin-only. Every write is validated (types, hard limits, admin bounds), versioned (a write
naming an older ``version`` is refused with 409 ``settings_changed``) and audited: admin
changes as a ``document_ai_settings_changed`` security event, Workspace changes in the
Workspace's audit trail and activity feed.

``document_ai_config(engine, workspace_id)`` is the one effective-settings lookup indexing
and search read: code defaults, then the installation's values, then the Workspace's
overrides clamped to the current bounds (an admin may tighten them after an owner chose).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import SecurityEventRecorder, User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .tables import DocumentAiSettingsRecord, WorkspaceDocumentAiRecord

AUDIT_ENTITY = "document_ai_settings"
SECURITY_EVENT = "document_ai_settings_changed"
CHUNKING_STRATEGIES = ("headings", "recursive", "semantic")

Value = bool | int | float | str


@dataclass(frozen=True)
class DocumentAiConfig:
    """The effective Document AI settings of one Workspace; the defaults are the code's."""

    # -- chunking (owners may override; a change applies to documents indexed from now on)
    chunking_strategy: str = "headings"
    chunk_size: int = 250
    chunk_overlap: int = 40
    contextual_headers: bool = True
    # -- indexing (admin-only)
    embed_batch: int = 32
    max_extracted_chars: int = 2_000_000
    max_pdf_pages: int = 1_000
    # -- retrieval (owners may override, except the RRF constant)
    top_k: int = 10
    fts_candidates: int = 20
    vector_candidates: int = 20
    fts_weight: float = 1.0
    vector_weight: float = 1.0
    rrf_k: int = 60
    mmr_lambda: float = 0.7
    min_text_rank: float = 0.01
    min_vector_similarity: float = 0.3
    # -- reranking
    rerank: bool = True
    rerank_top_n: int = 30

    def values(self) -> dict[str, Value]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


DEFAULTS = DocumentAiConfig()


@dataclass(frozen=True)
class Knob:
    key: str
    group: Literal["chunking", "indexing", "retrieval", "reranking"]
    kind: Literal["int", "float", "bool", "choice"]
    label: str
    description: str
    owner: bool = False
    """Owners may override it per Workspace."""
    reindex: bool = False
    """A change applies to documents indexed from then on: re-index to apply it to the rest."""
    low: float | None = None
    high: float | None = None
    """Hard limits, whoever sets the value."""
    choices: tuple[str, ...] = ()

    @property
    def default(self) -> Value:
        return getattr(DEFAULTS, self.key)

    @property
    def numeric(self) -> bool:
        return self.kind in ("int", "float")


KNOBS: tuple[Knob, ...] = (
    Knob(
        "chunking_strategy",
        "chunking",
        "choice",
        "Chunking strategy",
        "How documents are cut into chunks: by headings, recursively by paragraphs and "
        "sentences, or semantically where the topic changes.",
        owner=True,
        reindex=True,
        choices=CHUNKING_STRATEGIES,
    ),
    Knob(
        "chunk_size",
        "chunking",
        "int",
        "Chunk size",
        "Target tokens per chunk; a chunk never exceeds one and a half times it.",
        owner=True,
        reindex=True,
        low=50,
        high=1000,
    ),
    Knob(
        "chunk_overlap",
        "chunking",
        "int",
        "Chunk overlap",
        "Tokens a chunk repeats from the one before it; less than the chunk size.",
        owner=True,
        reindex=True,
        low=0,
        high=500,
    ),
    Knob(
        "contextual_headers",
        "chunking",
        "bool",
        "Chunk context",
        "Prepend a one-sentence context, written by the light model, to each chunk.",
        owner=True,
        reindex=True,
    ),
    Knob(
        "embed_batch",
        "indexing",
        "int",
        "Embedding batch size",
        "Chunks sent to the embedding model per call.",
        low=1,
        high=2048,
    ),
    Knob(
        "max_extracted_chars",
        "indexing",
        "int",
        "Extracted text cap",
        "Characters of text kept from one document.",
        low=10_000,
        high=20_000_000,
    ),
    Knob(
        "max_pdf_pages",
        "indexing",
        "int",
        "PDF page cap",
        "Pages read from one PDF.",
        low=1,
        high=10_000,
    ),
    Knob(
        "top_k",
        "retrieval",
        "int",
        "Results",
        "Chunks a search returns unless it asks for another number.",
        owner=True,
        low=1,
        high=50,
    ),
    Knob(
        "fts_candidates",
        "retrieval",
        "int",
        "Full-text candidates",
        "Chunks the full-text ranking contributes before fusion.",
        owner=True,
        low=1,
        high=200,
    ),
    Knob(
        "vector_candidates",
        "retrieval",
        "int",
        "Vector candidates",
        "Chunks the vector ranking contributes before fusion.",
        owner=True,
        low=1,
        high=200,
    ),
    Knob(
        "fts_weight",
        "retrieval",
        "float",
        "Full-text weight",
        "Weight of the full-text ranking in reciprocal rank fusion.",
        owner=True,
        low=0,
        high=10,
    ),
    Knob(
        "vector_weight",
        "retrieval",
        "float",
        "Vector weight",
        "Weight of the vector ranking in reciprocal rank fusion.",
        owner=True,
        low=0,
        high=10,
    ),
    Knob(
        "rrf_k",
        "retrieval",
        "int",
        "RRF constant",
        "The k of reciprocal rank fusion: higher flattens the gap between ranks.",
        low=1,
        high=1000,
    ),
    Knob(
        "mmr_lambda",
        "retrieval",
        "float",
        "Relevance vs variety (MMR)",
        "1 is pure relevance; lower values trade it for variety across documents and sections.",
        owner=True,
        low=0,
        high=1,
    ),
    Knob(
        "min_text_rank",
        "retrieval",
        "float",
        "Minimum full-text score",
        "The full-text rank a match needs to count as relevant.",
        owner=True,
        low=0,
        high=1,
    ),
    Knob(
        "min_vector_similarity",
        "retrieval",
        "float",
        "Minimum vector similarity",
        "The cosine similarity a vector match needs to count as relevant.",
        owner=True,
        low=0,
        high=1,
    ),
    Knob(
        "rerank",
        "reranking",
        "bool",
        "Rerank results",
        "Reorder the top results with the Workspace's rerank model, when it has one.",
        owner=True,
    ),
    Knob(
        "rerank_top_n",
        "reranking",
        "int",
        "Rerank top N",
        "How many of the fused top chunks the rerank model rescores.",
        low=1,
        high=200,
    ),
)
BY_KEY = {k.key: k for k in KNOBS}
OWNER_KNOBS = tuple(k for k in KNOBS if k.owner)

assert set(BY_KEY) == set(DEFAULTS.values()), "every setting has exactly one knob"


@dataclass(frozen=True)
class Bounds:
    min: float
    max: float


@dataclass(frozen=True)
class InstallationDocumentAi:
    values: dict[str, Value]
    """Every knob's installation value (the code default where an admin set none)."""
    bounds: dict[str, Bounds]
    """Every numeric owner knob's bounds (its hard limits where an admin set none)."""
    version: int
    """0: never changed."""
    updated_at: datetime | None


@dataclass(frozen=True)
class WorkspaceDocumentAi:
    overrides: dict[str, Value]
    inherited: dict[str, Value]
    """The installation's value of every knob owners may override."""
    effective: DocumentAiConfig
    bounds: dict[str, Bounds]
    version: int
    updated_at: datetime | None


def _invalid(code: str, message: str, field: str, **details: Any) -> ApiError:
    return ApiError(422, code, message, {"field": field, **details})


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _checked(knob: Knob, value: Any) -> Value:
    """``value`` as the knob's type, within its hard limits; 422 otherwise."""
    if knob.kind == "bool":
        if not isinstance(value, bool):
            raise _invalid("invalid_setting", f"{knob.label} is on or off.", knob.key)
        return value
    if knob.kind == "choice":
        if value not in knob.choices:
            raise _invalid(
                "invalid_setting",
                f"{knob.label} is one of: {', '.join(knob.choices)}.",
                knob.key,
            )
        return str(value)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid("invalid_setting", f"{knob.label} is a number.", knob.key)
    if knob.kind == "int" and not float(value).is_integer():
        raise _invalid("invalid_setting", f"{knob.label} is a whole number.", knob.key)
    assert knob.low is not None and knob.high is not None
    if not knob.low <= value <= knob.high:
        raise _invalid(
            "out_of_range",
            f"{knob.label} is between {_number(knob.low)} and {_number(knob.high)}.",
            knob.key,
            min=_number(knob.low),
            max=_number(knob.high),
        )
    return int(value) if knob.kind == "int" else float(value)


def _known(key: str) -> Knob:
    knob = BY_KEY.get(key)
    if knob is None:
        raise _invalid("unknown_setting", f"There is no setting called {key!r}.", key)
    return knob


def _check_overlap(values: Mapping[str, Value]) -> None:
    if int(values["chunk_overlap"]) >= int(values["chunk_size"]):
        raise _invalid(
            "invalid_setting",
            "The chunk overlap must be less than the chunk size.",
            "chunk_overlap",
        )


def _all_bounds(stored: Mapping[str, Any]) -> dict[str, Bounds]:
    out: dict[str, Bounds] = {}
    for knob in OWNER_KNOBS:
        if not knob.numeric:
            continue
        assert knob.low is not None and knob.high is not None
        given = stored.get(knob.key) or {}
        out[knob.key] = Bounds(given.get("min", knob.low), given.get("max", knob.high))
    return out


def _installation(record: DocumentAiSettingsRecord | None) -> InstallationDocumentAi:
    stored = dict(record.knob_values) if record else {}
    values = {k.key: stored.get(k.key, k.default) for k in KNOBS}
    return InstallationDocumentAi(
        values=values,
        bounds=_all_bounds(record.knob_bounds if record else {}),
        version=record.version if record else 0,
        updated_at=record.updated_at if record else None,
    )


def _effective(
    installation: InstallationDocumentAi, overrides: Mapping[str, Any]
) -> DocumentAiConfig:
    values = dict(installation.values)
    for knob in OWNER_KNOBS:
        if knob.key not in overrides:
            continue
        value = overrides[knob.key]
        bounds = installation.bounds.get(knob.key)
        if bounds is not None:
            value = min(max(value, bounds.min), bounds.max)
            value = int(value) if knob.kind == "int" else float(value)
        values[knob.key] = value
    config = DocumentAiConfig(**values)
    if config.chunk_overlap >= config.chunk_size:  # bounds tightened after the owner chose
        config = replace(config, chunk_overlap=config.chunk_size - 1)
    return config


def _overrides_of(db: Session, workspace_id: uuid.UUID) -> WorkspaceDocumentAiRecord | None:
    return db.get(WorkspaceDocumentAiRecord, workspace_id)


def document_ai_config(engine: sa.Engine, workspace_id: uuid.UUID) -> DocumentAiConfig:
    """The Workspace's effective Document AI settings, which indexing and search read."""
    with Session(engine) as db:
        installation = _installation(db.get(DocumentAiSettingsRecord, 1))
        record = _overrides_of(db, workspace_id)
        return _effective(installation, record.overrides if record else {})


def _stale(expected: int, current: int) -> None:
    if expected != current:
        raise ApiError(
            409,
            "settings_changed",
            "Someone changed these settings meanwhile. Reload them and try again.",
            {"version": current},
        )


@contextmanager
def _first_write_races() -> Iterator[None]:
    """Two first writes both find no row and both insert it: the loser is refused as a
    stale write (409), like any other concurrent change."""
    try:
        yield
    except sa.exc.IntegrityError:
        raise ApiError(
            409,
            "settings_changed",
            "Someone changed these settings meanwhile. Reload them and try again.",
            {},
        ) from None


class DocumentAiService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        events: SecurityEventRecorder | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._events = events

    # -- the installation (admins; the route checks) --------------------------------

    def installation(self) -> InstallationDocumentAi:
        with Session(self._engine) as db:
            return _installation(db.get(DocumentAiSettingsRecord, 1))

    def set_installation(
        self,
        admin: User,
        *,
        version: int,
        values: Mapping[str, Any],
        bounds: Mapping[str, Mapping[str, Any]],
        ip: str | None = None,
    ) -> InstallationDocumentAi:
        """Replace the installation's values and bounds (a knob left out takes its code
        default, a bound left out its hard limit). Recorded as a security event."""
        checked = {key: _checked(_known(key), value) for key, value in values.items()}
        checked_bounds: dict[str, dict[str, Value]] = {}
        for key, given in bounds.items():
            knob = _known(key)
            if not (knob.owner and knob.numeric):
                raise _invalid("not_overridable", f"Owners cannot override {knob.label}.", key)
            low = _checked(knob, given.get("min", knob.low))
            high = _checked(knob, given.get("max", knob.high))
            if low > high:  # type: ignore[operator]
                raise _invalid(
                    "invalid_bounds", f"The minimum of {knob.label} is above its maximum.", key
                )
            checked_bounds[key] = {"min": low, "max": high}
        result = _installation(
            DocumentAiSettingsRecord(
                knob_values=checked, knob_bounds=checked_bounds, version=0, updated_at=None
            )
        )
        _check_overlap(result.values)
        for key, b in result.bounds.items():
            value = result.values[key]
            if not b.min <= value <= b.max:  # type: ignore[operator]
                raise _invalid(
                    "out_of_bounds",
                    f"{BY_KEY[key].label} is outside the bounds set for owners.",
                    key,
                    min=_number(b.min),
                    max=_number(b.max),
                )
        with _first_write_races(), Session(self._engine) as db, db.begin():
            record = db.scalars(
                sa.select(DocumentAiSettingsRecord)
                .where(DocumentAiSettingsRecord.id == 1)
                .with_for_update()
            ).first()
            old = _installation(record)
            _stale(version, old.version)
            changed = sorted(
                k for k in result.values if result.values[k] != old.values[k]
            ) + sorted(f"bounds.{k}" for k in result.bounds if result.bounds[k] != old.bounds[k])
            if not changed:
                return old
            now = self._clock()
            if record is None:
                record = DocumentAiSettingsRecord(id=1, version=0)
                db.add(record)
            record.knob_values = checked
            record.knob_bounds = checked_bounds
            record.version = old.version + 1
            record.updated_by = admin.id
            record.updated_at = now
            if self._events is not None:
                self._events.record(
                    SECURITY_EVENT,
                    actor_id=admin.id,
                    metadata={
                        "version": record.version,
                        "old": _audited(old, changed),
                        "new": _audited(result, changed),
                    },
                    ip=ip,
                    db=db,
                )
            db.flush()
            return _installation(record)

    # -- a Workspace (members read, owners change) ------------------------------------

    def get(self, user: User, workspace_id: uuid.UUID) -> WorkspaceDocumentAi:
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return self._workspace(db, workspace_id)

    def set_overrides(
        self, user: User, workspace_id: uuid.UUID, *, version: int, overrides: Mapping[str, Any]
    ) -> WorkspaceDocumentAi:
        """Replace the Workspace's overrides (owners of an active Workspace; audited). Each
        is a knob owners may override, within the installation's bounds."""
        self._workspaces.authorize(user, Action.CHANGE_AI_SETTINGS, workspace_id)
        with _first_write_races(), Session(self._engine) as db, db.begin():
            installation = _installation(db.get(DocumentAiSettingsRecord, 1))
            checked: dict[str, Value] = {}
            for key, value in overrides.items():
                knob = _known(key)
                if not knob.owner:
                    raise _invalid(
                        "not_overridable",
                        f"Only an admin sets {knob.label}, for the whole installation.",
                        key,
                    )
                checked[key] = _checked(knob, value)
                bounds = installation.bounds.get(key)
                if bounds is not None and not bounds.min <= checked[key] <= bounds.max:  # type: ignore[operator]
                    raise _invalid(
                        "out_of_bounds",
                        f"{knob.label} is between {_number(bounds.min)} and "
                        f"{_number(bounds.max)} for this installation.",
                        key,
                        min=_number(bounds.min),
                        max=_number(bounds.max),
                    )
            _check_overlap({**installation.values, **checked})
            record = db.scalars(
                sa.select(WorkspaceDocumentAiRecord)
                .where(WorkspaceDocumentAiRecord.workspace_id == workspace_id)
                .with_for_update()
            ).first()
            old = dict(record.overrides) if record else {}
            _stale(version, record.version if record else 0)
            changed = sorted(k for k in {*old, *checked} if old.get(k) != checked.get(k))
            if not changed:
                return self._workspace(db, workspace_id)
            now = self._clock()
            if record is None:
                record = WorkspaceDocumentAiRecord(workspace_id=workspace_id, version=0)
                db.add(record)
            record.overrides = checked
            record.version += 1
            record.updated_at = now
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type=AUDIT_ENTITY,
                entity_id=workspace_id,
                old={k: old.get(k) for k in changed},
                new={k: checked.get(k) for k in changed},
                at=now,
            )
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="workspace.document_ai_settings_changed",
                object_type="workspace",
                object_id=workspace_id,
                details={"changed": changed},
                at=now,
            )
            db.flush()
            return self._workspace(db, workspace_id)

    @staticmethod
    def _workspace(db: Session, workspace_id: uuid.UUID) -> WorkspaceDocumentAi:
        installation = _installation(db.get(DocumentAiSettingsRecord, 1))
        record = _overrides_of(db, workspace_id)
        overrides = dict(record.overrides) if record else {}
        return WorkspaceDocumentAi(
            overrides=overrides,
            inherited={k.key: installation.values[k.key] for k in OWNER_KNOBS},
            effective=_effective(installation, overrides),
            bounds=installation.bounds,
            version=record.version if record else 0,
            updated_at=record.updated_at if record else None,
        )


def _audited(settings: InstallationDocumentAi, changed: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in changed:
        if key.startswith("bounds."):
            b = settings.bounds[key.removeprefix("bounds.")]
            out[key] = {"min": _number(b.min), "max": _number(b.max)}
        else:
            out[key] = settings.values[key]
    return out
