"""A Workspace's AI settings: its Workspace Models, the internal-only restriction and the
data-sharing level (spec §6.18, stories 160, 161).

Only owners change them (``Action.CHANGE_AI_SETTINGS``); every change is audited. The
Workspace Models are one embedding model and one or more agent and light models, one of
each the default, chosen from the models an admin enabled for each Model Role; the gateway
resolves every call's model from them (``RoleService.gateway_for_role``). The restriction
applies to every model role and is enforced in the gateway (``RoleService.metered_gateway``);
the level is exposed as one ``DataSharingPolicy`` that other modules ask before they put data
in a prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .tables import ModelRecord, ProviderRecord, WorkspaceAiSettingsRecord, WorkspaceModelRecord

AUDIT_ENTITY = "workspace_ai_settings"
ROLES = ("agent", "light", "embedding")


class DataSharingLevel(StrEnum):
    """What the AI may see, cumulative: each level includes every one before it."""

    METADATA = "metadata"
    PROFILES = "profiles"
    """+ profile statistics."""
    DOCUMENTS = "documents"
    """+ document text."""
    SAMPLES = "samples"
    """+ sample rows (enables ``run_source_query``)."""

    @property
    def rank(self) -> int:
        return tuple(DataSharingLevel).index(self)


@dataclass(frozen=True)
class DataSharingPolicy:
    """The one question other modules ask before sending data to a model:
    ``policy.allows(DataSharingLevel.SAMPLES)``."""

    level: DataSharingLevel

    def allows(self, needed: DataSharingLevel) -> bool:
        """True when the Workspace's level is ``needed`` or higher."""
        return self.level.rank >= needed.rank


@dataclass(frozen=True)
class WorkspaceModels:
    """A Workspace's Workspace Models: the models chosen for each Model Role. Every
    non-empty agent or light list has its default; embedding is one model or none."""

    agent: tuple[uuid.UUID, ...] = ()
    default_agent: uuid.UUID | None = None
    light: tuple[uuid.UUID, ...] = ()
    default_light: uuid.UUID | None = None
    """``None``: light tasks use the default agent model."""
    embedding: uuid.UUID | None = None
    """``None``: document search has no embedding model, so it matches text only."""


@dataclass(frozen=True)
class WorkspaceAiSettings:
    workspace_id: uuid.UUID
    internal_only: bool
    data_sharing_level: DataSharingLevel
    models: WorkspaceModels
    updated_at: datetime | None

    @property
    def policy(self) -> DataSharingPolicy:
        return DataSharingPolicy(self.data_sharing_level)

    @property
    def embedding_model_id(self) -> uuid.UUID | None:
        return self.models.embedding


@dataclass(frozen=True)
class ApprovedModel:
    """A model an owner may choose for a role: tested and enabled for it by an admin."""

    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool


@dataclass(frozen=True)
class ChosenModel:
    """One of the Workspace's models, for display."""

    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool
    is_default: bool
    limited: bool = False
    """Tested and without native tool calling."""


@dataclass(frozen=True)
class WorkspaceAiView:
    settings: WorkspaceAiSettings
    chosen: dict[str, list[ChosenModel]]
    """The Workspace Models per role, with names."""
    choices: dict[str, list[ApprovedModel]]
    """Per role, every model an owner may choose (an internal-only Workspace takes only
    the internal ones)."""
    embedding_locked: bool
    """The Workspace has indexed documents, so its embedding model is fixed (ADR 0005)."""
    internal_agent_available: bool
    """The installation has a tested agent model on an internal provider."""
    banner: bool
    """Show the "not internal-only" banner: the Workspace is not restricted and the
    installation has no internal agent model to restrict it to."""


@dataclass(frozen=True)
class NewWorkspaceChoices:
    internal_only: bool
    """A new Workspace starts internal-only: only internal models are offered."""
    choices: dict[str, list[ApprovedModel]]


def _model_rows(
    db: Session, workspace_id: uuid.UUID
) -> list[tuple[WorkspaceModelRecord, ModelRecord, ProviderRecord]]:
    rows = db.execute(
        sa.select(WorkspaceModelRecord, ModelRecord, ProviderRecord)
        .join(ModelRecord, ModelRecord.id == WorkspaceModelRecord.model_id)
        .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
        .where(WorkspaceModelRecord.workspace_id == workspace_id)
        .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
    )
    return [(w, m, p) for w, m, p in rows]


def _models(
    rows: list[tuple[WorkspaceModelRecord, ModelRecord, ProviderRecord]],
) -> WorkspaceModels:
    def ids(role: str) -> tuple[uuid.UUID, ...]:
        return tuple(w.model_id for w, _, _ in rows if w.role == role)

    def default(role: str) -> uuid.UUID | None:
        return next((w.model_id for w, _, _ in rows if w.role == role and w.is_default), None)

    return WorkspaceModels(
        agent=ids("agent"),
        default_agent=default("agent"),
        light=ids("light"),
        default_light=default("light"),
        embedding=default("embedding"),
    )


def load_settings(db: Session, workspace_id: uuid.UUID) -> WorkspaceAiSettings:
    """The Workspace's settings. A Workspace without a row (created before the hook was
    wired, e.g. in a bare test setup) is unrestricted at the lowest level."""
    record = db.get(WorkspaceAiSettingsRecord, workspace_id)
    models = _models(_model_rows(db, workspace_id))
    if record is None:
        return WorkspaceAiSettings(workspace_id, False, DataSharingLevel.METADATA, models, None)
    return WorkspaceAiSettings(
        workspace_id=record.workspace_id,
        internal_only=record.internal_only,
        data_sharing_level=DataSharingLevel(record.data_sharing_level),
        models=models,
        updated_at=record.updated_at,
    )


def default_model_id(db: Session, workspace_id: uuid.UUID, role: str) -> uuid.UUID | None:
    """The Workspace's default model for ``role``; ``None``: it has none."""
    return db.scalar(
        sa.select(WorkspaceModelRecord.model_id).where(
            WorkspaceModelRecord.workspace_id == workspace_id,
            WorkspaceModelRecord.role == role,
            WorkspaceModelRecord.is_default.is_(True),
        )
    )


def workspace_embedding_model_id(engine: sa.Engine, workspace_id: uuid.UUID) -> uuid.UUID | None:
    """The Workspace's embedding model; ``None``: it has none."""
    with Session(engine) as db:
        return default_model_id(db, workspace_id, "embedding")


def model_is_internal(db: Session, model_id: uuid.UUID) -> bool:
    internal = db.scalar(
        sa.select(ProviderRecord.is_internal)
        .join(ModelRecord, ModelRecord.provider_id == ProviderRecord.id)
        .where(ModelRecord.id == model_id)
    )
    return bool(internal)


def has_indexed_documents(db: Session, workspace_id: uuid.UUID) -> bool:
    """Whether any of the Workspace's documents carry vectors, which pin its embedding
    model (ADR 0005). The files module owns the chunks; this only reads them, by SQL."""
    return bool(
        db.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM document_chunks "
                "WHERE workspace_id = :w AND embedding_model_id IS NOT NULL)"
            ),
            {"w": workspace_id},
        )
    )


def enabled_models(db: Session, role: str) -> list[ApprovedModel]:
    """The models an admin enabled for ``role`` that passed "Test connection"."""
    rows = db.execute(
        sa.select(ModelRecord, ProviderRecord)
        .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
        .where(sa.literal(role) == sa.any_(ModelRecord.roles), ModelRecord.test_ok.is_(True))
        .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
    )
    return [ApprovedModel(m.id, m.name, p.name, p.is_internal) for m, p in rows]


def _internal_agent_available(db: Session) -> bool:
    return any(m.internal for m in enabled_models(db, "agent"))


def _invalid(message: str, field: str, code: str) -> ApiError:
    return ApiError(422, code, message, {"field": field})


@dataclass(frozen=True)
class _Fields:
    """The request fields errors name: creation and the settings form differ."""

    agent: str
    default_agent: str
    light: str
    default_light: str
    embedding: str = "embedding_model_id"


CREATE_FIELDS = _Fields("agent_model_id", "agent_model_id", "light_model_id", "light_model_id")
UPDATE_FIELDS = _Fields(
    "agent_model_ids", "default_agent_model_id", "light_model_ids", "default_light_model_id"
)


def _check_role(
    db: Session, role: str, ids: tuple[uuid.UUID, ...], field: str, *, internal_only: bool
) -> list[ApprovedModel]:
    """Refuse a model not enabled (and tested) for ``role``, or an external one for an
    internal-only Workspace. Returns what this Workspace may choose for the role."""
    enabled = {m.id: m for m in enabled_models(db, role)}
    for model_id in ids:
        if model_id not in enabled:
            raise _invalid(
                f"That model is not enabled for the {role} role.", field, "invalid_model_role"
            )
        if internal_only and not enabled[model_id].internal:
            raise _invalid(
                "An internal-only Workspace uses only models on internal providers.",
                field,
                "model_not_allowed",
            )
    return [m for m in enabled.values() if m.internal or not internal_only]


def _default(ids: tuple[uuid.UUID, ...], default: uuid.UUID | None, field: str) -> uuid.UUID | None:
    if default is None and len(ids) == 1:
        return ids[0]
    if (ids and default not in ids) or (not ids and default is not None):
        raise _invalid(
            "Mark one of the chosen models as the default.", field, "invalid_default_model"
        )
    return default


def validated_models(
    db: Session,
    *,
    internal_only: bool,
    agent: list[uuid.UUID],
    default_agent: uuid.UUID | None,
    light: list[uuid.UUID],
    default_light: uuid.UUID | None,
    embedding: uuid.UUID | None,
    fields: _Fields,
    embedding_required: bool,
) -> WorkspaceModels:
    """The one check for Workspace Models: every model enabled for its role and tested,
    internal-only respected, one default agent model (required whenever the Workspace may
    choose one) and one default light model when any light model is chosen."""
    if internal_only and enabled_models(db, "agent") and not _internal_agent_available(db):
        raise _invalid(
            "An internal-only Workspace needs an agent model on an internal provider.",
            "internal_only",
            "no_internal_model",
        )
    agent_ids, light_ids = tuple(dict.fromkeys(agent)), tuple(dict.fromkeys(light))
    offered_agent = _check_role(db, "agent", agent_ids, fields.agent, internal_only=internal_only)
    if not agent_ids and offered_agent:
        raise _invalid("Choose the default agent model.", fields.agent, "agent_model_required")
    _check_role(db, "light", light_ids, fields.light, internal_only=internal_only)
    offered_embedding = _check_role(
        db,
        "embedding",
        (embedding,) if embedding is not None else (),
        fields.embedding,
        internal_only=internal_only,
    )
    if embedding_required and embedding is None and offered_embedding:
        raise _invalid("Choose the embedding model.", fields.embedding, "embedding_model_required")
    return WorkspaceModels(
        agent=agent_ids,
        default_agent=_default(agent_ids, default_agent, fields.default_agent),
        light=light_ids,
        default_light=_default(light_ids, default_light, fields.default_light),
        embedding=embedding,
    )


def _store_models(db: Session, workspace_id: uuid.UUID, models: WorkspaceModels) -> None:
    db.execute(
        sa.delete(WorkspaceModelRecord).where(WorkspaceModelRecord.workspace_id == workspace_id)
    )
    rows = [
        *((m, "agent", m == models.default_agent) for m in models.agent),
        *((m, "light", m == models.default_light) for m in models.light),
        *([(models.embedding, "embedding", True)] if models.embedding is not None else []),
    ]
    db.add_all(
        WorkspaceModelRecord(workspace_id=workspace_id, model_id=m, role=role, is_default=d)
        for m, role, d in rows
    )
    db.flush()


def new_workspace_settings(
    db: Session, workspace_id: uuid.UUID, at: datetime
) -> WorkspaceAiSettingsRecord:
    """The settings a new Workspace starts with. Decided here, in code, not by a database
    default: internal-only when the installation has a tested internal agent model,
    otherwise not (the Workspace then shows a banner)."""
    return WorkspaceAiSettingsRecord(
        workspace_id=workspace_id,
        internal_only=_internal_agent_available(db),
        data_sharing_level=DataSharingLevel.METADATA.value,
        updated_at=at,
    )


def on_workspace_created(
    db: Session,
    workspace_id: uuid.UUID,
    at: datetime,
    *,
    embedding_model_id: uuid.UUID | None = None,
    agent_model_id: uuid.UUID | None = None,
    light_model_id: uuid.UUID | None = None,
) -> None:
    """The ``WorkspaceCreatedHook`` the composition root sets: gives the new Workspace its
    AI settings and Workspace Models in the transaction that creates it. The default agent
    model is required, and so is the embedding model, unless there is none the Workspace
    could choose; the light model is optional. A refused model fails the creation."""
    record = new_workspace_settings(db, workspace_id, at)
    db.add(record)
    db.flush()
    models = validated_models(
        db,
        internal_only=record.internal_only,
        agent=[agent_model_id] if agent_model_id else [],
        default_agent=agent_model_id,
        light=[light_model_id] if light_model_id else [],
        default_light=light_model_id,
        embedding=embedding_model_id,
        fields=CREATE_FIELDS,
        embedding_required=True,
    )
    _store_models(db, workspace_id, models)


def new_workspace_choices(engine: sa.Engine) -> NewWorkspaceChoices:
    """What the "New workspace" form offers for each role. A new Workspace starts
    internal-only when the installation has an internal agent model, so then only
    internal models are offered."""
    with Session(engine) as db:
        internal_only = _internal_agent_available(db)
        return NewWorkspaceChoices(
            internal_only,
            {
                role: [m for m in enabled_models(db, role) if m.internal or not internal_only]
                for role in ROLES
            },
        )


class WorkspaceAiService:
    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def settings(self, workspace_id: uuid.UUID) -> WorkspaceAiSettings:
        """The settings, for server-side callers. Authorizes nothing: callers have."""
        with Session(self._engine) as db:
            return load_settings(db, workspace_id)

    def policy(self, workspace_id: uuid.UUID) -> DataSharingPolicy:
        """The Workspace's data-sharing policy, for server-side callers."""
        return self.settings(workspace_id).policy

    def agent_model_id(self, workspace_id: uuid.UUID) -> uuid.UUID | None:
        """The model that answers by default: the Workspace's default agent model.
        Authorizes nothing: callers have."""
        with Session(self._engine) as db:
            return default_model_id(db, workspace_id, "agent")

    def check_chat_model(self, workspace_id: uuid.UUID, model_id: uuid.UUID) -> None:
        """A model a member may pick for a message: one of the Workspace's agent models.
        422 ``model_not_allowed`` otherwise."""
        with Session(self._engine) as db:
            if model_id not in load_settings(db, workspace_id).models.agent:
                raise _invalid(
                    "That model is not one of this Workspace's agent models.",
                    "model_id",
                    "model_not_allowed",
                )

    def usable_model_id(self, workspace_id: uuid.UUID, model_id: uuid.UUID) -> bool:
        """Whether a remembered pick can still be used: a chat model the Workspace may use."""
        try:
            self.check_chat_model(workspace_id, model_id)
        except ApiError:
            return False
        return True

    def model_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Display names of the models that still exist."""
        with Session(self._engine) as db:
            return {
                model_id: name
                for model_id, name in db.execute(
                    sa.select(ModelRecord.id, ModelRecord.name).where(ModelRecord.id.in_(ids))
                )
            }

    def get(self, user: User, workspace_id: uuid.UUID) -> WorkspaceAiView:
        """The settings and what an owner may choose from, for any member."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return self._view(db, workspace_id)

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        internal_only: bool,
        data_sharing_level: DataSharingLevel,
        agent_model_ids: list[uuid.UUID] | None = None,
        default_agent_model_id: uuid.UUID | None = None,
        light_model_ids: list[uuid.UUID] | None = None,
        default_light_model_id: uuid.UUID | None = None,
        embedding_model_id: uuid.UUID | None = None,
    ) -> WorkspaceAiView:
        """Owners only, while the Workspace is active. Replaces the settings and the
        Workspace Models (``validated_models``). The embedding model may change only while
        the Workspace has no indexed documents (ADR 0005): 422 ``embedding_model_fixed``."""
        self._workspaces.authorize(user, Action.CHANGE_AI_SETTINGS, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = db.scalars(
                sa.select(WorkspaceAiSettingsRecord)
                .where(WorkspaceAiSettingsRecord.workspace_id == workspace_id)
                .with_for_update()
            ).first()
            now = self._clock()
            if record is None:
                record = new_workspace_settings(db, workspace_id, now)
                db.add(record)
                db.flush()
            current = load_settings(db, workspace_id).models
            models = validated_models(
                db,
                internal_only=internal_only,
                agent=agent_model_ids or [],
                default_agent=default_agent_model_id,
                light=light_model_ids or [],
                default_light=default_light_model_id,
                embedding=embedding_model_id,
                fields=UPDATE_FIELDS,
                embedding_required=False,
            )
            if models.embedding != current.embedding and has_indexed_documents(db, workspace_id):
                raise _invalid(
                    "The Workspace has indexed documents, so its embedding model is fixed.",
                    "embedding_model_id",
                    "embedding_model_fixed",
                )
            old = _audited(record, current)
            record.internal_only = internal_only
            record.data_sharing_level = data_sharing_level.value
            record.updated_at = now
            _store_models(db, workspace_id, models)
            new = _audited(record, models)
            changed = sorted(k for k in new if new[k] != old[k])
            if changed:
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type=AUDIT_ENTITY,
                    entity_id=workspace_id,
                    old={k: old[k] for k in changed},
                    new={k: new[k] for k in changed},
                    at=now,
                )
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="workspace.ai_settings_changed",
                    object_type="workspace",
                    object_id=workspace_id,
                    details={"changed": changed},
                    at=now,
                )
            return self._view(db, workspace_id)

    @staticmethod
    def _view(db: Session, workspace_id: uuid.UUID) -> WorkspaceAiView:
        settings = load_settings(db, workspace_id)
        internal_available = _internal_agent_available(db)
        chosen: dict[str, list[ChosenModel]] = {role: [] for role in ROLES}
        for w, m, p in _model_rows(db, workspace_id):
            chosen[w.role].append(ChosenModel(
                    m.id,
                    m.name,
                    p.name,
                    p.is_internal,
                    w.is_default,
                    m.test_ok is True and m.supports_tools is False,
                )
            )
        return WorkspaceAiView(
            settings=settings,
            chosen=chosen,
            choices={role: enabled_models(db, role) for role in ROLES},
            embedding_locked=has_indexed_documents(db, workspace_id),
            internal_agent_available=internal_available,
            banner=not settings.internal_only and not internal_available,
        )


def _audited(record: WorkspaceAiSettingsRecord, models: WorkspaceModels) -> dict[str, object]:
    def ids(values: tuple[uuid.UUID, ...]) -> list[str]:
        return sorted(map(str, values))

    def one(value: uuid.UUID | None) -> str | None:
        return str(value) if value else None

    return {
        "internal_only": record.internal_only,
        "data_sharing_level": record.data_sharing_level,
        "agent_model_ids": ids(models.agent),
        "default_agent_model_id": one(models.default_agent),
        "light_model_ids": ids(models.light),
        "default_light_model_id": one(models.default_light),
        "embedding_model_id": one(models.embedding),
    }
