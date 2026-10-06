"""``/api/v1/admin/llm``: LLM providers, their models and setup status, admins only
(spec §6.18, stories 155, 156, 158). No response ever carries an API key: it reports
``has_api_key``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.errors import ApiError

from .service import (
    API_KEY_MAX_LENGTH,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    ROLES,
    ModelInput,
    ProviderInput,
    ProviderService,
)
from .service import Model as ModelView
from .service import Provider as ProviderView
from .service import SetupStatus as SetupStatusView
from .tables import MODEL_NAME_MAX_LENGTH, NAME_MAX_LENGTH, URL_MAX_LENGTH

router = APIRouter(prefix="/admin/llm", tags=["llm"])


def llm_admin(user: CurrentUser) -> User:
    if not can(user, Action.MANAGE_SYSTEM_SETTINGS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


LlmAdmin = Annotated[User, Depends(llm_admin)]


def provider_service(request: Request) -> ProviderService:
    state = request.app.state
    factory = getattr(state.services, "llm_adapters", None)
    options = {"adapters": factory} if factory is not None else {}
    return ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=state.services.clock,
        **options,
    )


ProviderServiceDep = Annotated[ProviderService, Depends(provider_service)]

ModelRole = Literal["agent", "light", "embedding"]


class LlmModel(BaseModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    name: str = Field(description="The model name sent to the provider.")
    roles: list[ModelRole]
    context_window: int | None = Field(
        description="Tokens: the admin's figure, or what the server's metadata reported."
    )
    tool_calling: bool | None = Field(description="From the last test; null: not tested.")
    streaming: bool | None
    json_schema: bool | None = Field(description="Supports JSON-schema output.")
    embedding_dimension: int | None
    limited: bool = Field(
        description="Tested and without native tool calling: DAWAM works around it, with "
        "less reliable tool use."
    )
    test_ok: bool | None = Field(description="Null: not tested since it last changed.")
    test_error_code: str | None = Field(
        description="`auth`, `rate_limit`, `context_overflow`, `unavailable`, `bad_request` "
        "or `invalid_response` when the test failed."
    )
    test_error: str | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LlmProvider(BaseModel):
    id: uuid.UUID
    name: str
    adapter: Literal["openai_compatible", "anthropic"]
    base_url: str
    has_api_key: bool = Field(description="Whether a key is stored. It is never returned.")
    internal: bool = Field(description="True for a provider inside your own infrastructure.")
    timeout_seconds: int
    models: list[LlmModel]
    created_at: datetime
    updated_at: datetime


class LlmProviderRequest(BaseModel):
    name: str = Field(max_length=NAME_MAX_LENGTH * 2)
    adapter: Literal["openai_compatible", "anthropic"] = "openai_compatible"
    base_url: str = Field(
        max_length=URL_MAX_LENGTH * 2,
        description="Including the version prefix, e.g. `http://vllm:8000/v1` or "
        "`https://api.anthropic.com/v1`.",
    )
    api_key: str | None = Field(
        default=None,
        max_length=API_KEY_MAX_LENGTH * 2,
        description="Leave out to keep the stored key (allowed only for the base URL it was "
        "saved for, otherwise 422 `api_key_required`). An empty string clears it.",
    )
    internal: bool = Field(
        description="Internal or external; Workspaces set to internal-only "
        "refuse external providers."
    )
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1, le=MAX_TIMEOUT_SECONDS)


class LlmModelRequest(BaseModel):
    name: str = Field(max_length=MODEL_NAME_MAX_LENGTH * 2)
    roles: list[ModelRole] = Field(
        min_length=1,
        max_length=len(ROLES),
        description="`agent`, `light`, or `embedding` (on its own).",
    )
    context_window: int | None = Field(
        default=None,
        ge=1,
        description="Tokens. Leave out to take the server's figure when it reports one.",
    )


class LlmSetupStatus(BaseModel):
    complete: bool = Field(
        description="False until an agent model is registered and its test passed: show a "
        "banner to admins."
    )
    has_agent_model: bool
    has_tested_agent_model: bool
    has_internal_agent_model: bool = Field(
        description="False: new Workspaces will default to not internal-only."
    )


def _provider_out(view: ProviderView) -> LlmProvider:
    return LlmProvider(
        id=view.id,
        name=view.name,
        adapter=view.adapter,  # type: ignore[arg-type]
        base_url=view.base_url,
        has_api_key=view.has_api_key,
        internal=view.is_internal,
        timeout_seconds=view.timeout_seconds,
        models=[_model_out(m) for m in view.models],
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _model_out(view: ModelView) -> LlmModel:
    return LlmModel(
        id=view.id,
        provider_id=view.provider_id,
        name=view.name,
        roles=view.roles,  # type: ignore[arg-type]
        context_window=view.context_window,
        tool_calling=view.tool_calling,
        streaming=view.streaming,
        json_schema=view.json_schema,
        embedding_dimension=view.embedding_dimension,
        limited=view.limited,
        test_ok=view.test_ok,
        test_error_code=view.test_error_code,
        test_error=view.test_error,
        last_tested_at=view.last_tested_at,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _provider_input(body: LlmProviderRequest) -> ProviderInput:
    return ProviderInput(
        name=body.name,
        adapter=body.adapter,
        base_url=body.base_url,
        api_key=body.api_key,
        is_internal=body.internal,
        timeout_seconds=body.timeout_seconds,
    )


def _model_input(body: LlmModelRequest) -> ModelInput:
    return ModelInput(name=body.name, roles=body.roles, context_window=body.context_window)


class LlmProviderList(BaseModel):
    items: list[LlmProvider]


@router.get("/providers", operation_id="listLlmProviders")
def list_providers(_admin: LlmAdmin, providers: ProviderServiceDep) -> LlmProviderList:
    """Every provider with its models, each flagged internal or external (admins only)."""
    return LlmProviderList(items=[_provider_out(p) for p in providers.list_providers()])


@router.post("/providers", operation_id="createLlmProvider", status_code=201)
def create_provider(
    body: LlmProviderRequest, _admin: LlmAdmin, providers: ProviderServiceDep
) -> LlmProvider:
    """Register a provider (admins only). The key is sealed before it is stored. 409
    `provider_name_taken`; 422 `invalid_llm_config`."""
    return _provider_out(providers.create_provider(_provider_input(body)))


@router.get("/providers/{provider_id}", operation_id="getLlmProvider")
def get_provider(
    provider_id: uuid.UUID, _admin: LlmAdmin, providers: ProviderServiceDep
) -> LlmProvider:
    """One provider with its models (admins only). 404 `provider_not_found`."""
    return _provider_out(providers.get_provider(provider_id))


@router.put("/providers/{provider_id}", operation_id="updateLlmProvider")
def update_provider(
    provider_id: uuid.UUID,
    body: LlmProviderRequest,
    _admin: LlmAdmin,
    providers: ProviderServiceDep,
) -> LlmProvider:
    """Replace a provider's settings (admins only). Changing the base URL or the key
    drops its models' test results."""
    return _provider_out(providers.update_provider(provider_id, _provider_input(body)))


@router.delete("/providers/{provider_id}", operation_id="deleteLlmProvider", status_code=204)
def delete_provider(
    provider_id: uuid.UUID, _admin: LlmAdmin, providers: ProviderServiceDep
) -> Response:
    """Remove a provider and its models (admins only)."""
    providers.delete_provider(provider_id)
    return Response(status_code=204)


@router.post("/providers/{provider_id}/models", operation_id="addLlmModel", status_code=201)
def add_model(
    provider_id: uuid.UUID,
    body: LlmModelRequest,
    _admin: LlmAdmin,
    providers: ProviderServiceDep,
) -> LlmModel:
    """Register a model of the provider (admins only). 409 `model_exists`."""
    return _model_out(providers.add_model(provider_id, _model_input(body)))


@router.put("/models/{model_id}", operation_id="updateLlmModel")
def update_model(
    model_id: uuid.UUID, body: LlmModelRequest, _admin: LlmAdmin, providers: ProviderServiceDep
) -> LlmModel:
    """Replace a model's name, roles and context window (admins only). A new name or
    roles drop its test result."""
    return _model_out(providers.update_model(model_id, _model_input(body)))


@router.delete("/models/{model_id}", operation_id="deleteLlmModel", status_code=204)
def delete_model(model_id: uuid.UUID, _admin: LlmAdmin, providers: ProviderServiceDep) -> Response:
    providers.delete_model(model_id)
    return Response(status_code=204)


@router.post("/models/{model_id}/test", operation_id="testLlmModel")
def test_model(model_id: uuid.UUID, _admin: LlmAdmin, providers: ProviderServiceDep) -> LlmModel:
    """ "Test connection" (admins only): a short prompt and a dummy tool call, recording
    tool, streaming and JSON-schema support, the context window and, for an embedding
    model, its vector dimension. Always 200: a failure is `test_ok: false` with
    `test_error_code` and a safe `test_error`."""
    return _model_out(providers.test_model(model_id))


@router.get("/setup", operation_id="getLlmSetup")
def get_setup(_admin: LlmAdmin, providers: ProviderServiceDep) -> LlmSetupStatus:
    """Whether AI is set up: at least one agent model registered and tested (admins
    only). Until then the admin console shows a banner."""
    status: SetupStatusView = providers.setup_status()
    return LlmSetupStatus(
        complete=status.complete,
        has_agent_model=status.has_agent_model,
        has_tested_agent_model=status.has_tested_agent_model,
        has_internal_agent_model=status.has_internal_agent_model,
    )
