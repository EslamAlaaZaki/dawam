"""A Workspace's AI settings: its agent model, the internal-only restriction and the
data-sharing level (spec §6.18, stories 160, 161).

Only owners change them (``Action.CHANGE_AI_SETTINGS``); every change is audited. The
restriction applies to every model role and is enforced in the gateway
(``RoleService.metered_gateway``); the level is exposed as one ``DataSharingPolicy`` that
other modules ask before they put data in a prompt.
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

from .tables import (
    ModelRecord,
    ProviderRecord,
    SettingsRecord,
    WorkspaceAiSettingsRecord,
    WorkspaceAllowedModelRecord,
)

AUDIT_ENTITY = "workspace_ai_settings"


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
class WorkspaceAiSettings:
    workspace_id: uuid.UUID
    internal_only: bool
    data_sharing_level: DataSharingLevel
    agent_model_id: uuid.UUID | None
    """The Workspace's own agent model; ``None``: the installation's."""
    light_model_id: uuid.UUID | None
    """The Workspace's own light model; ``None``: the installation's."""
    embedding_model_id: uuid.UUID | None
    """The Workspace's embedding model, fixed at creation (ADR 0005); ``None``: none was
    registered then, and indexing uses the first tested embedding model."""
    updated_at: datetime | None

    @property
    def policy(self) -> DataSharingPolicy:
        return DataSharingPolicy(self.data_sharing_level)


@dataclass(frozen=True)
class ApprovedModel:
    """An admin-approved agent model: registered, with the agent role, and tested."""

    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool


@dataclass(frozen=True)
class AllowedModelChoice:
    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool
    allowed: bool
    """In effect: listed (or nothing is listed) and, if internal-only, internal."""


@dataclass(frozen=True)
class AllowedModels:
    model_ids: list[uuid.UUID]
    """The admin's explicit list; empty: every registered model."""
    models: list[AllowedModelChoice]


@dataclass(frozen=True)
class WorkspaceAiView:
    settings: WorkspaceAiSettings
    approved_models: list[ApprovedModel]
    light_models: list[ApprovedModel]
    """The chat models an owner may choose as the light model."""
    installation_agent_model_id: uuid.UUID | None
    installation_light_model_id: uuid.UUID | None
    embedding_model_name: str | None
    """The Workspace's fixed embedding model, for display."""
    model_names: dict[uuid.UUID, str]
    """Names of the installation's and the Workspace's chosen models, for display."""
    internal_agent_available: bool
    """The installation has an agent model on an internal provider."""
    banner: bool
    """Show the "not internal-only" banner: the Workspace is not restricted and the
    installation has no internal agent model to restrict it to."""


def load_settings(db: Session, workspace_id: uuid.UUID) -> WorkspaceAiSettings:
    """The Workspace's settings. A Workspace without a row (created before the hook was
    wired, e.g. in a bare test setup) is unrestricted at the lowest level."""
    record = db.get(WorkspaceAiSettingsRecord, workspace_id)
    if record is None:
        return WorkspaceAiSettings(
            workspace_id, False, DataSharingLevel.METADATA, None, None, None, None
        )
    return WorkspaceAiSettings(
        workspace_id=record.workspace_id,
        internal_only=record.internal_only,
        data_sharing_level=DataSharingLevel(record.data_sharing_level),
        agent_model_id=record.agent_model_id,
        light_model_id=record.light_model_id,
        embedding_model_id=record.embedding_model_id,
        updated_at=record.updated_at,
    )


def workspace_embedding_model_id(engine: sa.Engine, workspace_id: uuid.UUID) -> uuid.UUID | None:
    """The embedding model fixed at the Workspace's creation; ``None``: it has none."""
    with Session(engine) as db:
        return load_settings(db, workspace_id).embedding_model_id


def model_is_internal(db: Session, model_id: uuid.UUID) -> bool:
    internal = db.scalar(
        sa.select(ProviderRecord.is_internal)
        .join(ModelRecord, ModelRecord.provider_id == ProviderRecord.id)
        .where(ModelRecord.id == model_id)
    )
    return bool(internal)


def _listed_model_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.scalars(
            sa.select(WorkspaceAllowedModelRecord.model_id).where(
                WorkspaceAllowedModelRecord.workspace_id == workspace_id
            )
        )
    )


def allowed_model_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """The one check for which models a Workspace may use: Allowed Models (every registered
    model unless narrowed) combined with the internal-only flag, which drops every
    external model whatever the list says."""
    listed = _listed_model_ids(db, workspace_id)
    query = sa.select(ModelRecord.id).join(
        ProviderRecord, ProviderRecord.id == ModelRecord.provider_id
    )
    if load_settings(db, workspace_id).internal_only:
        query = query.where(ProviderRecord.is_internal.is_(True))
    if listed:
        query = query.where(ModelRecord.id.in_(listed))
    return set(db.scalars(query))


def model_is_listed(db: Session, workspace_id: uuid.UUID, model_id: uuid.UUID) -> bool:
    """False only when an admin narrowed the Workspace's Allowed Models and ``model_id`` is
    not among them. The internal-only flag is enforced separately, when a call is made."""
    listed = _listed_model_ids(db, workspace_id)
    return not listed or model_id in listed


def _installation_agent(db: Session) -> uuid.UUID | None:
    return db.scalar(sa.select(SettingsRecord.agent_model_id).where(SettingsRecord.id == 1))


def _installation_light(db: Session) -> uuid.UUID | None:
    return db.scalar(sa.select(SettingsRecord.light_model_id).where(SettingsRecord.id == 1))


def _invalid(message: str, field: str, code: str) -> ApiError:
    return ApiError(422, code, message, {"field": field})


def _installation_embedding(db: Session) -> uuid.UUID | None:
    return db.scalar(sa.select(SettingsRecord.embedding_model_id).where(SettingsRecord.id == 1))


def new_workspace_settings(
    db: Session, workspace_id: uuid.UUID, at: datetime
) -> WorkspaceAiSettingsRecord:
    """The settings a new Workspace starts with. Decided here, in code, not by a database
    default: internal-only when the installation's agent model is internal, otherwise not
    (the Workspace then shows a banner); the installation's embedding model, which the
    Workspace keeps even if the installation's later changes (ADR 0005)."""
    agent = _installation_agent(db)
    return WorkspaceAiSettingsRecord(
        workspace_id=workspace_id,
        internal_only=agent is not None and model_is_internal(db, agent),
        data_sharing_level=DataSharingLevel.METADATA.value,
        agent_model_id=None,
        light_model_id=None,
        embedding_model_id=None,
        updated_at=at,
    )


def _pin_default_embedding(db: Session, record: WorkspaceAiSettingsRecord) -> None:
    """Pin the installation's embedding model, unless the Workspace's one allowed-models
    check (internal-only included) refuses it: then the Workspace has none."""
    db.flush()
    default = _installation_embedding(db)
    if default is not None and default in allowed_model_ids(db, record.workspace_id):
        record.embedding_model_id = default


def on_workspace_created(
    db: Session,
    workspace_id: uuid.UUID,
    at: datetime,
    embedding_model_id: uuid.UUID | None = None,
) -> None:
    """The ``WorkspaceCreatedHook`` the composition root sets: gives the new Workspace its
    AI settings in the transaction that creates it. A chosen embedding model must be a
    tested embedding model that passes the Workspace's one allowed-models check; it is
    fixed from then on."""
    record = new_workspace_settings(db, workspace_id, at)
    db.add(record)
    _pin_default_embedding(db, record)
    if embedding_model_id is not None:
        model = db.get(ModelRecord, embedding_model_id)
        if model is None or "embedding" not in model.roles or model.test_ok is not True:
            raise _invalid(
                "That model is not a tested embedding model.",
                "embedding_model_id",
                "invalid_model_role",
            )
        if embedding_model_id not in allowed_model_ids(db, workspace_id):
            raise _invalid(
                "That model is not in the Workspace's Allowed Models.",
                "embedding_model_id",
                "model_not_allowed",
            )
        record.embedding_model_id = embedding_model_id


@dataclass(frozen=True)
class EmbeddingChoices:
    models: list[ApprovedModel]
    """Tested embedding models a new Workspace may be created with."""
    default_model_id: uuid.UUID | None
    """The installation's embedding model: the pre-selection."""


def embedding_choices(engine: sa.Engine) -> EmbeddingChoices:
    """What the "New workspace" form offers. A new Workspace starts internal-only when the
    installation's agent model is internal, so then only internal models are offered."""
    with Session(engine) as db:
        agent = _installation_agent(db)
        internal_only = agent is not None and model_is_internal(db, agent)
        rows = db.execute(
            sa.select(ModelRecord, ProviderRecord)
            .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
            .where(sa.literal("embedding") == sa.any_(ModelRecord.roles))
            .where(ModelRecord.test_ok.is_(True))
            .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
        )
        models = [
            ApprovedModel(m.id, m.name, p.name, p.is_internal)
            for m, p in rows
            if p.is_internal or not internal_only
        ]
        default = _installation_embedding(db)
        return EmbeddingChoices(models, default if default in {m.id for m in models} else None)


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
        """The model that answers by default: the Workspace's own agent model, else the
        installation's. Authorizes nothing: callers have."""
        with Session(self._engine) as db:
            return load_settings(db, workspace_id).agent_model_id or _installation_agent(db)

    def check_chat_model(self, workspace_id: uuid.UUID, model_id: uuid.UUID) -> None:
        """A model a member may pick for a message: a tested chat model (never an embedding
        model) that the Workspace may use, i.e. in its Allowed Models and, if it is
        internal-only, internal. 422 ``invalid_model_role`` / ``model_not_allowed``."""
        with Session(self._engine) as db:
            model = db.get(ModelRecord, model_id)
            if (
                model is None
                or not {"agent", "light"} & set(model.roles)
                or model.test_ok is not True
            ):
                raise _invalid("That is not a chat model.", "model_id", "invalid_model_role")
            if model_id not in allowed_model_ids(db, workspace_id):
                raise _invalid(
                    "That model is not in the Workspace's Allowed Models.",
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
            return self._view(db, load_settings(db, workspace_id))

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        internal_only: bool,
        data_sharing_level: DataSharingLevel,
        agent_model_id: uuid.UUID | None,
        light_model_id: uuid.UUID | None = None,
        embedding_model_id: uuid.UUID | None = None,
    ) -> WorkspaceAiView:
        """Owners only. The agent model must be an approved agent model and the light model
        a tested chat model, both within the Workspace's Allowed Models; an internal-only
        Workspace must end up with an internal agent model (422 ``no_internal_model``). The
        embedding model is fixed at creation (ADR 0005): naming a different one is refused
        with 422 ``embedding_model_fixed``; leaving it out is fine."""
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
                _pin_default_embedding(db, record)
            approved = {m.id for m in self._approved(db)}
            if agent_model_id is not None and agent_model_id in approved - allowed_model_ids(
                db, workspace_id
            ):
                raise _invalid(
                    "That model is not in the Workspace's Allowed Models.",
                    "agent_model_id",
                    "model_not_allowed",
                )
            if agent_model_id is not None and agent_model_id not in approved:
                raise _invalid(
                    "That model is not an approved agent model.",
                    "agent_model_id",
                    "invalid_model_role",
                )
            if light_model_id is not None:
                light = db.get(ModelRecord, light_model_id)
                if light is None or "light" not in light.roles or light.test_ok is not True:
                    raise _invalid(
                        "That model is not an approved chat model.",
                        "light_model_id",
                        "invalid_model_role",
                    )
                if light_model_id not in allowed_model_ids(db, workspace_id):
                    raise _invalid(
                        "That model is not in the Workspace's Allowed Models.",
                        "light_model_id",
                        "model_not_allowed",
                    )
            if embedding_model_id is not None and embedding_model_id != record.embedding_model_id:
                raise _invalid(
                    "The Workspace's embedding model is fixed when it is created.",
                    "embedding_model_id",
                    "embedding_model_fixed",
                )
            effective = agent_model_id or _installation_agent(db)
            if internal_only and effective is not None and not model_is_internal(db, effective):
                raise _invalid(
                    "An internal-only Workspace needs an agent model on an internal provider.",
                    "internal_only",
                    "no_internal_model",
                )
            old = _audited(record)
            record.internal_only = internal_only
            record.data_sharing_level = data_sharing_level.value
            record.agent_model_id = agent_model_id
            record.light_model_id = light_model_id
            record.updated_at = now
            new = _audited(record)
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
            db.flush()
            return self._view(db, load_settings(db, workspace_id))

    def _view(self, db: Session, settings: WorkspaceAiSettings) -> WorkspaceAiView:
        agent = _installation_agent(db)
        internal_available = agent is not None and model_is_internal(db, agent)
        allowed = allowed_model_ids(db, settings.workspace_id)
        installation_light = _installation_light(db)
        named = {
            i
            for i in (
                agent,
                installation_light,
                settings.agent_model_id,
                settings.light_model_id,
                settings.embedding_model_id,
            )
            if i is not None
        }
        names = {
            model_id: name
            for model_id, name in db.execute(
                sa.select(ModelRecord.id, ModelRecord.name).where(ModelRecord.id.in_(named))
            )
        }
        return WorkspaceAiView(
            settings=settings,
            approved_models=[m for m in self._approved(db) if m.id in allowed],
            light_models=[m for m in self._chat_models(db) if m.id in allowed],
            installation_agent_model_id=agent,
            installation_light_model_id=installation_light,
            embedding_model_name=(
                names.get(settings.embedding_model_id) if settings.embedding_model_id else None
            ),
            model_names=names,
            internal_agent_available=internal_available,
            banner=not settings.internal_only and not internal_available,
        )

    def allowed_models(self, workspace_id: uuid.UUID) -> AllowedModels:
        """The explicit list and every registered model with whether the Workspace may use
        it. Authorizes nothing: the admin API has."""
        with Session(self._engine) as db:
            return self._allowed_view(db, workspace_id)

    def set_allowed_models(
        self, user: User, workspace_id: uuid.UUID, model_ids: list[uuid.UUID]
    ) -> AllowedModels:
        """Replace the list (empty: every registered model), audited. The admin API
        authorizes. 422 ``invalid_model`` for an id that is not registered."""
        wanted = set(model_ids)
        with Session(self._engine) as db, db.begin():
            known = set(db.scalars(sa.select(ModelRecord.id).where(ModelRecord.id.in_(wanted))))
            if known != wanted:
                raise _invalid("Choose registered models.", "model_ids", "invalid_model")
            old = _listed_model_ids(db, workspace_id)
            db.execute(
                sa.delete(WorkspaceAllowedModelRecord).where(
                    WorkspaceAllowedModelRecord.workspace_id == workspace_id
                )
            )
            db.add_all(
                WorkspaceAllowedModelRecord(workspace_id=workspace_id, model_id=m) for m in wanted
            )
            db.flush()
            if old != wanted:
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="workspace_allowed_models",
                    entity_id=workspace_id,
                    old={"model_ids": sorted(map(str, old))},
                    new={"model_ids": sorted(map(str, wanted))},
                    at=self._clock(),
                )
            return self._allowed_view(db, workspace_id)

    @staticmethod
    def _allowed_view(db: Session, workspace_id: uuid.UUID) -> AllowedModels:
        allowed = allowed_model_ids(db, workspace_id)
        rows = db.execute(
            sa.select(ModelRecord, ProviderRecord)
            .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
            .where(sa.func.cardinality(ModelRecord.roles) > 0)  # no role: offered nowhere
            .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
        )
        return AllowedModels(
            model_ids=sorted(_listed_model_ids(db, workspace_id), key=str),
            models=[
                AllowedModelChoice(m.id, m.name, p.name, p.is_internal, m.id in allowed)
                for m, p in rows
            ],
        )

    @staticmethod
    def _chat_models(db: Session) -> list[ApprovedModel]:
        rows = db.execute(
            sa.select(ModelRecord, ProviderRecord)
            .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
            .where(sa.literal("light") == sa.any_(ModelRecord.roles))
            .where(ModelRecord.test_ok.is_(True))
            .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
        )
        return [ApprovedModel(m.id, m.name, p.name, p.is_internal) for m, p in rows]

    @staticmethod
    def _approved(db: Session) -> list[ApprovedModel]:
        rows = db.execute(
            sa.select(ModelRecord, ProviderRecord)
            .join(ProviderRecord, ProviderRecord.id == ModelRecord.provider_id)
            .where(sa.literal("agent") == sa.any_(ModelRecord.roles), ModelRecord.test_ok.is_(True))
            .order_by(sa.func.lower(ProviderRecord.name), sa.func.lower(ModelRecord.name))
        )
        return [ApprovedModel(m.id, m.name, p.name, p.is_internal) for m, p in rows]


def _audited(record: WorkspaceAiSettingsRecord) -> dict[str, object]:
    return {
        "internal_only": record.internal_only,
        "data_sharing_level": record.data_sharing_level,
        "agent_model_id": str(record.agent_model_id) if record.agent_model_id else None,
        "light_model_id": str(record.light_model_id) if record.light_model_id else None,
    }
