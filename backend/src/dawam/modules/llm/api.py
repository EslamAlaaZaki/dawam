"""``/api/v1/admin/llm``: LLM providers, their models, setup status, model roles, token
budgets and usage, admins only (spec §6.18, stories 155-159, 162). No response ever carries an
API key: it reports ``has_api_key``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import AuthService, CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.errors import ApiError

from .role_service import MAX_BUDGET, RoleService, UsageTotals
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
    adapter: str
    base_url: str
    has_api_key: bool = Field(description="Whether a key is stored. It is never returned.")
    internal: bool = Field(description="True for a provider inside your own infrastructure.")
    timeout_seconds: int
    models: list[LlmModel]
    created_at: datetime
    updated_at: datetime


class LlmProviderRequest(BaseModel):
    name: str = Field(max_length=NAME_MAX_LENGTH * 2)
    adapter: str = Field(
        default="openai_compatible",
        description="`openai_compatible`, `anthropic`, `azure_openai` or a plugin kind.",
    )
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


# -- model roles, budgets and usage -----------------------------------------------------


def role_service(request: Request, providers: ProviderServiceDep) -> RoleService:
    state = request.app.state
    return RoleService(state.engine, providers=providers, clock=state.services.clock)


RoleServiceDep = Annotated[RoleService, Depends(role_service)]


def workspace_names(request: Request) -> WorkspaceService:
    state = request.app.state
    return WorkspaceService(state.engine, clock=state.services.clock)


WorkspaceNamesDep = Annotated[WorkspaceService, Depends(workspace_names)]


def auth_users(request: Request) -> AuthService:
    state = request.app.state
    return AuthService(state.engine, state.settings, clock=state.services.clock)


AuthUsersDep = Annotated[AuthService, Depends(auth_users)]


class LlmRoles(BaseModel):
    agent_model_id: uuid.UUID | None = Field(
        description="Required for AI to work; null only until an admin assigns one."
    )
    light_model_id: uuid.UUID | None = Field(
        description="Null: light tasks (titles, short summaries) use the agent model."
    )
    embedding_model_id: uuid.UUID | None
    reindex_needed: bool = Field(
        description="True after the embedding model or its vector dimension changed: "
        "documents must be indexed again."
    )
    reindex_reason: Literal["embedding_model_changed", "embedding_dimension_changed"] | None
    reindex_flagged_at: datetime | None


class LlmRolesRequest(BaseModel):
    agent_model_id: uuid.UUID
    light_model_id: uuid.UUID | None = None
    embedding_model_id: uuid.UUID | None = None


class LlmWorkspaceBudget(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str | None = Field(description="Null if the Workspace no longer exists.")
    monthly_token_budget: int


class LlmBudgets(BaseModel):
    installation_monthly_token_budget: int | None = Field(description="Null: unlimited.")
    workspaces: list[LlmWorkspaceBudget]


class LlmBudgetRequest(BaseModel):
    monthly_token_budget: int | None = Field(
        ge=0,
        le=MAX_BUDGET,
        description="Tokens per calendar month (UTC). Null removes the installation's limit.",
    )


class LlmWorkspaceBudgetRequest(BaseModel):
    monthly_token_budget: int = Field(ge=0, le=MAX_BUDGET)


class LlmUsageTotals(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int
    estimated_calls: int = Field(description="Calls whose tokens DAWAM estimated.")


class LlmWorkspaceUsage(BaseModel):
    workspace_id: uuid.UUID | None = Field(
        description="Null: calls made for no Workspace, or one since deleted."
    )
    workspace_name: str | None
    totals: LlmUsageTotals
    tokens_by_role: dict[str, int]
    monthly_token_budget: int | None


class LlmUserUsage(BaseModel):
    user_id: uuid.UUID | None
    email: str | None
    display_name: str | None
    totals: LlmUsageTotals
    tokens_by_role: dict[str, int]


class LlmUsage(BaseModel):
    month: str = Field(description="`YYYY-MM` (UTC).")
    totals: LlmUsageTotals
    installation_monthly_token_budget: int | None
    workspaces: list[LlmWorkspaceUsage]
    users: list[LlmUserUsage]


def _totals_out(totals: UsageTotals) -> LlmUsageTotals:
    return LlmUsageTotals(
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        total_tokens=totals.total_tokens,
        calls=totals.calls,
        estimated_calls=totals.estimated_calls,
    )


def _roles_out(roles: RoleService) -> LlmRoles:
    view = roles.assignments()
    return LlmRoles(
        agent_model_id=view.agent_model_id,
        light_model_id=view.light_model_id,
        embedding_model_id=view.embedding_model_id,
        reindex_needed=view.reindex_needed,
        reindex_reason=view.reindex_reason,  # type: ignore[arg-type]
        reindex_flagged_at=view.reindex_flagged_at,
    )


def _budgets_out(roles: RoleService, workspaces: WorkspaceService) -> LlmBudgets:
    budgets = roles.budgets()
    names = workspaces.names_by_id(b.workspace_id for b in budgets.workspaces)
    return LlmBudgets(
        installation_monthly_token_budget=budgets.installation,
        workspaces=[
            LlmWorkspaceBudget(
                workspace_id=b.workspace_id,
                workspace_name=names.get(b.workspace_id),
                monthly_token_budget=b.monthly_token_budget,
            )
            for b in budgets.workspaces
        ],
    )


@router.get("/roles", operation_id="getLlmRoles")
def get_roles(_admin: LlmAdmin, roles: RoleServiceDep) -> LlmRoles:
    """Which model fills each role, and whether re-indexing is needed (admins only)."""
    return _roles_out(roles)


@router.put("/roles", operation_id="setLlmRoles")
def set_roles(body: LlmRolesRequest, _admin: LlmAdmin, roles: RoleServiceDep) -> LlmRoles:
    """Assign models to the roles (admins only). The agent role is required; every model
    must be registered and have passed "Test connection". Switching the embedding model
    sets `reindex_needed`. 422 `invalid_model_role` or `model_not_tested`."""
    roles.assign_roles(
        agent_model_id=body.agent_model_id,
        light_model_id=body.light_model_id,
        embedding_model_id=body.embedding_model_id,
    )
    return _roles_out(roles)


@router.get("/budgets", operation_id="getLlmBudgets")
def get_budgets(
    _admin: LlmAdmin, roles: RoleServiceDep, workspaces: WorkspaceNamesDep
) -> LlmBudgets:
    """The installation's monthly token budget and every Workspace's own (admins only)."""
    return _budgets_out(roles, workspaces)


@router.put("/budgets/installation", operation_id="setLlmInstallationBudget")
def set_installation_budget(
    body: LlmBudgetRequest,
    _admin: LlmAdmin,
    roles: RoleServiceDep,
    workspaces: WorkspaceNamesDep,
) -> LlmBudgets:
    """Set (or, with null, remove) the installation's monthly token budget (admins
    only). Once it is used up, AI calls fail with 429 `token_budget_exhausted`; the rest
    of DAWAM keeps working."""
    roles.set_installation_budget(body.monthly_token_budget)
    return _budgets_out(roles, workspaces)


@router.put("/budgets/workspaces/{workspace_id}", operation_id="setLlmWorkspaceBudget")
def set_workspace_budget(
    workspace_id: uuid.UUID,
    body: LlmWorkspaceBudgetRequest,
    _admin: LlmAdmin,
    roles: RoleServiceDep,
    workspaces: WorkspaceNamesDep,
) -> LlmBudgets:
    """Set a Workspace's own monthly token budget (admins only). 404 `workspace_not_found`."""
    if workspace_id not in workspaces.names_by_id([workspace_id]):
        raise ApiError(404, "workspace_not_found", "Workspace not found.")
    roles.set_workspace_budget(workspace_id, body.monthly_token_budget)
    return _budgets_out(roles, workspaces)


@router.delete(
    "/budgets/workspaces/{workspace_id}", operation_id="clearLlmWorkspaceBudget", status_code=204
)
def clear_workspace_budget(
    workspace_id: uuid.UUID, _admin: LlmAdmin, roles: RoleServiceDep
) -> Response:
    """Remove a Workspace's own budget (admins only): only the installation's applies."""
    roles.clear_workspace_budget(workspace_id)
    return Response(status_code=204)


@router.get("/usage", operation_id="getLlmUsage")
def get_usage(
    _admin: LlmAdmin,
    roles: RoleServiceDep,
    workspaces: WorkspaceNamesDep,
    users: AuthUsersDep,
    month: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}$", description="`YYYY-MM` (UTC); default: this month."),
    ] = None,
) -> LlmUsage:
    """AI usage in a month, in total, per Workspace and per user, with tokens per model
    role (admins only)."""
    report = roles.usage_report(month)
    budgets = {b.workspace_id: b.monthly_token_budget for b in roles.budgets().workspaces}
    names = workspaces.names_by_id(w.workspace_id for w in report.workspaces if w.workspace_id)
    people = users.users_by_id(u.user_id for u in report.users if u.user_id)
    return LlmUsage(
        month=report.month,
        totals=_totals_out(report.totals),
        installation_monthly_token_budget=report.installation_budget,
        workspaces=[
            LlmWorkspaceUsage(
                workspace_id=w.workspace_id,
                workspace_name=names.get(w.workspace_id) if w.workspace_id else None,
                totals=_totals_out(w.totals),
                tokens_by_role=w.by_role,
                monthly_token_budget=budgets.get(w.workspace_id) if w.workspace_id else None,
            )
            for w in report.workspaces
        ],
        users=[
            LlmUserUsage(
                user_id=u.user_id,
                email=people[u.user_id].email if u.user_id in people else None,
                display_name=people[u.user_id].display_name if u.user_id in people else None,
                totals=_totals_out(u.totals),
                tokens_by_role=u.by_role,
            )
            for u in report.users
        ],
    )
