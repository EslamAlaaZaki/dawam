"""``/api/v1/workspaces/{workspace_id}/ai-settings``: a Workspace's Workspace Models,
internal-only restriction and data-sharing level (spec §6.18, stories 160, 161).

Handlers only translate HTTP to ``WorkspaceAiService`` calls; the service authorizes
through the workspaces module's policy (any member reads, owners of an active Workspace
change).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, SecurityEventRecorder
from dawam.modules.workspaces import WorkspaceService

from .workspace_ai import (
    ApprovedModel,
    ChosenModel,
    DataSharingLevel,
    WorkspaceAiService,
    WorkspaceAiView,
    new_workspace_choices,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/ai-settings", tags=["llm"])


def workspace_ai_service(request: Request) -> WorkspaceAiService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(
        state.engine, clock=clock, events=SecurityEventRecorder(state.engine, clock=clock)
    )
    return WorkspaceAiService(
        state.engine,
        workspaces=workspaces,
        clock=clock,
        on_embedding_changed=getattr(state, "on_embedding_model_changed", None),
    )


WorkspaceAiServiceDep = Annotated[WorkspaceAiService, Depends(workspace_ai_service)]


class ModelChoice(BaseModel):
    id: uuid.UUID
    name: str
    provider_name: str
    internal: bool = Field(description="True when the provider is inside your infrastructure.")


class WorkspaceModel(ModelChoice):
    is_default: bool
    limited: bool = Field(
        description="Tested and without native tool calling: less reliable tool use."
    )


class ModelChoices(BaseModel):
    agent: list[ModelChoice]
    light: list[ModelChoice]
    embedding: list[ModelChoice]
    vision: list[ModelChoice]


class LightFeature(BaseModel):
    key: str
    name: str
    description: str
    enabled: bool = Field(description="On: the Workspace has a light model to run it.")


LIGHT_FEATURES = (
    (
        "conversation_compaction",
        "Conversation compaction",
        "Summarises the oldest turns of a long assistant conversation instead of dropping them.",
    ),
    (
        "chunk_context",
        "Passage context",
        "Prepends a one-sentence context to each document passage before it is embedded and "
        "full-text indexed.",
    ),
)
"""The features that use the light role; each light-role ticket adds its own entry. They are
all off while the Workspace has no light model."""


ChunkingStrategy = Literal["headings", "recursive", "semantic"]


class WorkspaceAiSettings(BaseModel):
    internal_only: bool = Field(
        description="Every model role (agent, light, embedding, vision) is refused an external "
        "provider."
    )
    data_sharing_level: DataSharingLevel = Field(
        description="What the AI may see, cumulative: `metadata` < `profiles` < `documents` "
        "< `samples`."
    )
    chunking_strategy: ChunkingStrategy = Field(
        description="How documents are cut into passages: `headings` (sections, the default), "
        "`recursive` (paragraphs, then sentences, then words) or `semantic` (split where the "
        "embedding similarity of neighbouring sentences drops). Applies to documents indexed "
        "from now on; re-index to apply it to the others."
    )
    agent_models: list[WorkspaceModel] = Field(
        description="The Workspace's agent models; the default answers unless a message "
        "names another."
    )
    light_models: list[WorkspaceModel] = Field(
        description="The Workspace's light models; none: the light-role features are off."
    )
    light_features: list[LightFeature] = Field(
        description="Each feature that uses the light role, and whether it is on."
    )
    embedding_model: WorkspaceModel | None = Field(
        description="The Workspace's embedding model; null: document search matches text only."
    )
    embedding_rebuilding: bool = Field(
        description="The documents are being embedded again with a new embedding model; "
        "document search matches text only until they all are."
    )
    vision_model: WorkspaceModel | None = Field(
        description="The Workspace's vision model, which reads scanned pages when the "
        "data-sharing level allows sending documents to its provider (an internal one always "
        "may); null, or not allowed: the self-hosted OCR engine reads them."
    )
    choices: ModelChoices = Field(
        description="Per role, the tested models an admin enabled for it, for an owner to choose "
        "from (an internal-only Workspace takes only internal ones)."
    )
    internal_agent_available: bool = Field(
        description="The installation has a tested agent model on an internal provider."
    )
    banner: bool = Field(
        description="Show a banner: the Workspace is not internal-only and the installation "
        "has no internal agent model."
    )


class WorkspaceAiSettingsRequest(BaseModel):
    """Model fields left out keep their current value."""

    internal_only: bool
    data_sharing_level: DataSharingLevel
    chunking_strategy: ChunkingStrategy | None = Field(
        default=None, description="Left out: keeps the current strategy."
    )
    agent_model_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Models enabled for the agent role; required when there is any to choose.",
    )
    default_agent_model_id: uuid.UUID | None = Field(
        default=None, description="One of `agent_model_ids`; optional when there is only one."
    )
    light_model_ids: list[uuid.UUID] = Field(
        default_factory=list, description="Models enabled for the light role; optional."
    )
    default_light_model_id: uuid.UUID | None = Field(
        default=None, description="One of `light_model_ids`; optional when there is only one."
    )
    embedding_model_id: uuid.UUID | None = Field(
        default=None,
        description="A model enabled for the embedding role. Changing it once documents are "
        "indexed embeds them again in the background; search matches text only meanwhile.",
    )
    vision_model_id: uuid.UUID | None = Field(
        default=None, description="A model enabled for the vision role; optional."
    )


def _choice(m: ApprovedModel) -> ModelChoice:
    return ModelChoice(id=m.id, name=m.name, provider_name=m.provider_name, internal=m.internal)


def _chosen(m: ChosenModel) -> WorkspaceModel:
    return WorkspaceModel(
        id=m.id,
        name=m.name,
        provider_name=m.provider_name,
        internal=m.internal,
        is_default=m.is_default,
        limited=m.limited,
    )


def _choices(choices: dict[str, list[ApprovedModel]]) -> ModelChoices:
    return ModelChoices(
        agent=[_choice(m) for m in choices["agent"]],
        light=[_choice(m) for m in choices["light"]],
        embedding=[_choice(m) for m in choices["embedding"]],
        vision=[_choice(m) for m in choices["vision"]],
    )


def _out(view: WorkspaceAiView) -> WorkspaceAiSettings:
    settings = view.settings
    embedding = view.chosen["embedding"]
    vision = view.chosen["vision"]
    return WorkspaceAiSettings(
        internal_only=settings.internal_only,
        data_sharing_level=settings.data_sharing_level,
        chunking_strategy=settings.chunking_strategy,
        agent_models=[_chosen(m) for m in view.chosen["agent"]],
        light_models=[_chosen(m) for m in view.chosen["light"]],
        light_features=[
            LightFeature(
                key=key, name=name, description=description, enabled=bool(view.chosen["light"])
            )
            for key, name, description in LIGHT_FEATURES
        ],
        embedding_model=_chosen(embedding[0]) if embedding else None,
        embedding_rebuilding=view.embedding_rebuilding,
        vision_model=_chosen(vision[0]) if vision else None,
        choices=_choices(view.choices),
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
    """Replace the settings and the Workspace Models (owners of an active Workspace; audited);
    model fields left out keep their current value.
    422 `invalid_model_role` for a model not enabled (and tested) for its role;
    422 `model_not_allowed` for an external model in an internal-only Workspace;
    422 `no_internal_model` when internal-only has no internal agent model to use;
    422 `agent_model_required` without an agent model; 422 `invalid_default_model` when the
    default is not one of the chosen models. A different embedding model once documents are
    indexed starts a `reindex_documents` job that embeds them again (`embedding_rebuilding`)."""
    sent = body.model_fields_set
    current = service.settings(workspace_id).models
    agents = "agent_model_ids" in sent
    lights = "light_model_ids" in sent
    return _out(
        service.update(
            user,
            workspace_id,
            internal_only=body.internal_only,
            data_sharing_level=body.data_sharing_level,
            chunking_strategy=body.chunking_strategy,
            agent_model_ids=body.agent_model_ids if agents else list(current.agent),
            default_agent_model_id=(
                body.default_agent_model_id
                if agents or "default_agent_model_id" in sent
                else current.default_agent
            ),
            light_model_ids=body.light_model_ids if lights else list(current.light),
            default_light_model_id=(
                body.default_light_model_id
                if lights or "default_light_model_id" in sent
                else current.default_light
            ),
            embedding_model_id=(
                body.embedding_model_id if "embedding_model_id" in sent else current.embedding
            ),
            vision_model_id=body.vision_model_id if "vision_model_id" in sent else current.vision,
        )
    )


new_workspace_router = APIRouter(prefix="/ai/new-workspace-models", tags=["llm"])


class NewWorkspaceModels(BaseModel):
    internal_only: bool = Field(
        description="A new Workspace starts internal-only, so only internal models are offered."
    )
    choices: ModelChoices = Field(
        description="Per role, the models a new Workspace may be created with. No embedding "
        "model to offer: the Workspace is created without one and document search matches "
        "text only until an owner sets it."
    )


@new_workspace_router.get("", operation_id="listNewWorkspaceModels")
def list_new_workspace_models(request: Request, user: CurrentUser) -> NewWorkspaceModels:
    """The models the "New workspace" form offers for each role (any signed-in user)."""
    choices = new_workspace_choices(request.app.state.engine)
    return NewWorkspaceModels(
        internal_only=choices.internal_only, choices=_choices(choices.choices)
    )
