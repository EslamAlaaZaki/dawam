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

from .workspace_ai import DataSharingLevel, WorkspaceAiService, WorkspaceAiView

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
    effective_agent_model_id: uuid.UUID | None = Field(
        description="The agent model in use: the Workspace's, else the installation's."
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


def _out(view: WorkspaceAiView) -> WorkspaceAiSettings:
    settings = view.settings
    return WorkspaceAiSettings(
        internal_only=settings.internal_only,
        data_sharing_level=settings.data_sharing_level,
        agent_model_id=settings.agent_model_id,
        effective_agent_model_id=settings.agent_model_id or view.installation_agent_model_id,
        approved_models=[
            ApprovedAgentModel(
                id=m.id, name=m.name, provider_name=m.provider_name, internal=m.internal
            )
            for m in view.approved_models
        ],
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
    that is not an approved agent model; 422 `no_internal_model` when internal-only has
    no internal agent model to use."""
    return _out(
        service.update(
            user,
            workspace_id,
            internal_only=body.internal_only,
            data_sharing_level=body.data_sharing_level,
            agent_model_id=body.agent_model_id,
        )
    )
