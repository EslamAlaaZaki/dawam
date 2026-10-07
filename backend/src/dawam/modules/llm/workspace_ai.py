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

from .tables import ModelRecord, ProviderRecord, SettingsRecord, WorkspaceAiSettingsRecord

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
class WorkspaceAiView:
    settings: WorkspaceAiSettings
    approved_models: list[ApprovedModel]
    installation_agent_model_id: uuid.UUID | None
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
        return WorkspaceAiSettings(workspace_id, False, DataSharingLevel.METADATA, None, None)
    return WorkspaceAiSettings(
        workspace_id=record.workspace_id,
        internal_only=record.internal_only,
        data_sharing_level=DataSharingLevel(record.data_sharing_level),
        agent_model_id=record.agent_model_id,
        updated_at=record.updated_at,
    )


def model_is_internal(db: Session, model_id: uuid.UUID) -> bool:
    internal = db.scalar(
        sa.select(ProviderRecord.is_internal)
        .join(ModelRecord, ModelRecord.provider_id == ProviderRecord.id)
        .where(ModelRecord.id == model_id)
    )
    return bool(internal)


def _installation_agent(db: Session) -> uuid.UUID | None:
    return db.scalar(sa.select(SettingsRecord.agent_model_id).where(SettingsRecord.id == 1))


def _invalid(message: str, field: str, code: str) -> ApiError:
    return ApiError(422, code, message, {"field": field})


def new_workspace_settings(
    db: Session, workspace_id: uuid.UUID, at: datetime
) -> WorkspaceAiSettingsRecord:
    """The settings a new Workspace starts with. Decided here, in code, not by a database
    default: internal-only when the installation's agent model is internal, otherwise not
    (the Workspace then shows a banner)."""
    agent = _installation_agent(db)
    return WorkspaceAiSettingsRecord(
        workspace_id=workspace_id,
        internal_only=agent is not None and model_is_internal(db, agent),
        data_sharing_level=DataSharingLevel.METADATA.value,
        agent_model_id=None,
        updated_at=at,
    )


def on_workspace_created(db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
    """The ``WorkspaceCreatedHook`` the composition root sets: gives the new Workspace its
    AI settings in the transaction that creates it."""
    db.add(new_workspace_settings(db, workspace_id, at))


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
    ) -> WorkspaceAiView:
        """Owners only. The model must be an approved agent model; an internal-only
        Workspace must end up with an internal agent model (422 ``no_internal_model``)."""
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
            if agent_model_id is not None and agent_model_id not in {
                m.id for m in self._approved(db)
            }:
                raise _invalid(
                    "That model is not an approved agent model.",
                    "agent_model_id",
                    "invalid_model_role",
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
        return WorkspaceAiView(
            settings=settings,
            approved_models=self._approved(db),
            installation_agent_model_id=agent,
            internal_agent_available=internal_available,
            banner=not settings.internal_only and not internal_available,
        )

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
    }
