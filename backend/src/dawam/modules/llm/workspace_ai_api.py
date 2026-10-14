"""``/api/v1/workspaces/{workspace_id}/ai-settings``: a Workspace's agent model,
internal-only restriction and data-sharing level (spec stories 160, 161).

Handlers only translate HTTP to ``WorkspaceAiService`` calls; the service authorizes
through the workspaces module's policy (any member reads, owners change).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, SecurityEventRecorder
from dawam.modules.workspaces import WorkspaceService

from .workspace_ai import (
    ApprovedModel,
    DataSharingLevel,
    WorkspaceAiService,
    WorkspaceAiView,
    embedding_choices,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/ai-settings", tags=["llm"])


def workspace_ai_service(request: Request) -> WorkspaceAiService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(
        state.engine, clock=clock, events=SecurityEventRecorder(state.engine, clock=clock)
    )
    return WorkspaceAiService(state.engine, workspaces=workspaces, clock=clock)


WorkspaceAiServiceDep = Annotated[WorkspaceAiService, Depends(workspace_ai_service)]


class ApprovedAgentModel(BaseModel):
    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool = Field(description="True when the provider is inside your infrastructure.")


class WorkspaceAiSettings(BaseModel):
    internal_only: bool = Field(
        description="Every model role (agent, light, embedding) is refused an external provider."
    )
    data_sharing_level: DataSharingLevel = Field(
        description="What the AI may see, cumulative: `metadata` < `profiles` < `documents` "
        "< `samples`."
    )
    agent_model_id: uuid.UUID | None = Field(
        description="The Workspace's own agent model; null: the installation's."
    )
    light_model_id: uuid.UUID | None = Field(
        description="The Workspace's own light model; null: the installation's."
    )
    embedding_model_id: uuid.UUID | None = Field(
        description="The Workspace's embedding model, chosen at creation and fixed (read-only); "
        "null: none was registered then."
    )
    embedding_model_name: str | None
    effective_agent_model_id: uuid.UUID | None = Field(
        description="The agent model in use: the Workspace's, else the installation's."
    )
    effective_agent_model_name: str | None
    effective_light_model_id: uuid.UUID | None = Field(
        description="The light model in use: the Workspace's, else the installation's; "
        "null: light tasks use the agent model."
    )
    effective_light_model_name: str | None
    light_models: list[ApprovedAgentModel] = Field(
        description="The chat models an owner may choose as the light model."
    )
    approved_models: list[ApprovedAgentModel] = Field(
        description="The admin-approved agent models an owner may choose from."
    )
    internal_agent_available: bool = Field(
        description="The installation has an agent model on an internal provider."
    )
    banner: bool = Field(
        description="Show a banner: the Workspace is not internal-only and the installation "
        "has no internal agent model."
    )


class WorkspaceAiSettingsRequest(BaseModel):
    internal_only: bool
    data_sharing_level: DataSharingLevel
    agent_model_id: uuid.UUID | None = Field(
        default=None, description="An approved agent model; null: use the installation's."
    )
    light_model_id: uuid.UUID | None = Field(
        default=None, description="A tested chat model; null: use the installation's."
    )
    embedding_model_id: uuid.UUID | None = Field(
        default=None,
        description="Must be left out or equal the Workspace's current embedding model: it is "
        "fixed at creation.",
    )


def _model_out(m: ApprovedModel) -> ApprovedAgentModel:
    return ApprovedAgentModel(
        id=m.id, name=m.name, provider_name=m.provider_name, internal=m.internal
    )


def _out(view: WorkspaceAiView) -> WorkspaceAiSettings:
    settings = view.settings
    agent = settings.agent_model_id or view.installation_agent_model_id
    light = settings.light_model_id or view.installation_light_model_id
    return WorkspaceAiSettings(
        internal_only=settings.internal_only,
        data_sharing_level=settings.data_sharing_level,
        agent_model_id=settings.agent_model_id,
        light_model_id=settings.light_model_id,
        embedding_model_id=settings.embedding_model_id,
        embedding_model_name=view.embedding_model_name,
        effective_agent_model_id=agent,
        effective_agent_model_name=view.model_names.get(agent) if agent else None,
        effective_light_model_id=light,
        effective_light_model_name=view.model_names.get(light) if light else None,
        approved_models=[_model_out(m) for m in view.approved_models],
        light_models=[_model_out(m) for m in view.light_models],
        internal_agent_available=view.internal_agent_available,
        banner=view.banner,
    )


@router.get("", operation_id="getWorkspaceAiSettings")
def get_ai_settings(
    workspace_id: uuid.UUID, user: CurrentUser, service: WorkspaceAiServiceDep
) -> WorkspaceAiSettings:
    """The Workspace's AI settings and the models an owner may choose from (any member)."""
    return _out(service.get(user, workspace_id))


@router.put("", operation_id="setWorkspaceAiSettings")
def set_ai_settings(
    workspace_id: uuid.UUID,
    body: WorkspaceAiSettingsRequest,
    user: CurrentUser,
    service: WorkspaceAiServiceDep,
) -> WorkspaceAiSettings:
    """Replace the settings (owners only; audited). 422 `invalid_model_role` for a model
    that is not an approved agent model (or a light model that is not a tested chat model);
    422 `model_not_allowed` for a model outside the Workspace's Allowed Models;
    422 `embedding_model_fixed` for a different embedding model (it cannot change);
    422 `no_internal_model` when internal-only has no internal agent model to use."""
    return _out(
        service.update(
            user,
            workspace_id,
            internal_only=body.internal_only,
            data_sharing_level=body.data_sharing_level,
            agent_model_id=body.agent_model_id,
            light_model_id=body.light_model_id,
            embedding_model_id=body.embedding_model_id,
        )
    )


embedding_router = APIRouter(prefix="/ai/embedding-models", tags=["llm"])


class EmbeddingChoicesOut(BaseModel):
    models: list[ApprovedAgentModel] = Field(
        description="Tested embedding models a new Workspace may be created with."
    )
    default_model_id: uuid.UUID | None = Field(
        description="The installation's embedding model: pre-select it."
    )


@embedding_router.get("", operation_id="listEmbeddingModels")
def list_embedding_models(request: Request, user: CurrentUser) -> EmbeddingChoicesOut:
    """The embedding models the "New workspace" form offers, and the installation's default
    (any signed-in user). A Workspace keeps its choice for good."""
    choices = embedding_choices(request.app.state.engine)
    return EmbeddingChoicesOut(
        models=[_model_out(m) for m in choices.models], default_model_id=choices.default_model_id
    )
