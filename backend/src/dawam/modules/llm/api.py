"""``/api/v1/admin/llm``: LLM providers, their models and their Model Roles, setup status,
token budgets and usage, admins only (spec §6.18, stories 155-159, 162). No response ever carries an
API key: it reports ``has_api_key``."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from dawam.modules.auth import AuthService, CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.errors import ApiError

from .model_removal import (
    WorkspaceRef,
    WorkspaceReplacement,
    model_usage,
    release_roles,
    remove_model,
)
from .role_service import (
    MAX_BUDGET,
    MAX_COST_BUDGET,
    Cost,
    CostBudget,
    ModelPrice,
    RoleService,
    UsageTotals,
)
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

ModelRole = Literal["agent", "light", "embedding", "vision"]


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
    warnings: list[str] = Field(
        description="Why the model may not suit its roles, for the admin: an unknown or "
        "too-small context window for the agent role, and (right after Test connection) "
        "an Ollama `num_ctx` below the minimum with how to raise it. Not blocking."
    )
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
    context_window: int | None = Field(
        default=None,
        ge=1,
        description="Tokens. Leave out to take the server's figure when it reports one.",
    )


class LlmModelUpdateRequest(BaseModel):
    context_window: int | None = Field(
        default=None,
        ge=1,
        description="Tokens. Null: take the server's figure on the next test.",
    )


class LlmWorkspaceReplacement(BaseModel):
    workspace_id: uuid.UUID
    agent_model_id: uuid.UUID | None = None
    light_model_id: uuid.UUID | None = None
    embedding_model_id: uuid.UUID | None = None


class LlmModelRolesRequest(BaseModel):
    roles: list[ModelRole] = Field(
        max_length=len(ROLES),
        description="Any combination of `agent`, `light` and `embedding`; none: the model is "
        "offered nowhere.",
    )
    workspace_replacements: list[LlmWorkspaceReplacement] = Field(
        default_factory=list,
        description="Per Workspace and role, the replacement for a role being unticked where "
        "the model is the Workspace's default or only model for it.",
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
        warnings=list(view.warnings),
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
    return ModelInput(name=body.name, context_window=body.context_window)


class LlmListedModel(BaseModel):
    name: str
    registered: bool = Field(description="Already registered under this provider.")


class LlmProviderModels(BaseModel):
    supported: bool = Field(description="False when the adapter cannot list its models.")
    models: list[LlmListedModel]
    error: str | None = Field(
        default=None, description="Why listing failed (bad key, server down); no secrets."
    )
    error_code: str | None = Field(
        default=None, description="The gateway's common error code when listing failed."
    )


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


@router.get("/providers/{provider_id}/available-models", operation_id="listLlmProviderModels")
def list_provider_models(
    provider_id: uuid.UUID, _admin: LlmAdmin, providers: ProviderServiceDep
) -> LlmProviderModels:
    """The models the provider itself lists, with a `registered` flag, using its stored
    key (never returned). A listing failure is reported in `error`, not as an HTTP error.
    404 `provider_not_found`."""
    listing = providers.list_provider_models(provider_id)
    return LlmProviderModels(
        supported=listing.supported,
        models=[LlmListedModel(name=m.name, registered=m.registered) for m in listing.models],
        error=listing.error,
        error_code=listing.error_code,
    )


@router.post("/providers/{provider_id}/models", operation_id="addLlmModel", status_code=201)
def add_model(
    provider_id: uuid.UUID,
    body: LlmModelRequest,
    _admin: LlmAdmin,
    providers: ProviderServiceDep,
) -> LlmModel:
    """Register a model of the provider with no role (admins only): its roles are set in
    the roles table. 409 `model_exists`."""
    return _model_out(providers.add_model(provider_id, _model_input(body)))


@router.put("/models/{model_id}", operation_id="updateLlmModel")
def update_model(
    model_id: uuid.UUID,
    body: LlmModelUpdateRequest,
    _admin: LlmAdmin,
    providers: ProviderServiceDep,
) -> LlmModel:
    """Change a model's context window (admins only); nothing else about it is editable."""
    return _model_out(
        providers.update_model(model_id, ModelInput(name="", context_window=body.context_window))
    )


@router.put("/models/{model_id}/roles", operation_id="setLlmModelRoles")
def set_model_roles(
    model_id: uuid.UUID,
    body: LlmModelRolesRequest,
    request: Request,
    _admin: LlmAdmin,
    providers: ProviderServiceDep,
) -> LlmModel:
    """Set the roles a model fills, any combination (admins only). Ticking `embedding` runs
    an embedding test and stores the vector dimension: 422 `embedding_test_failed` with the
    provider's error when the model cannot embed. Giving a model the `agent` role needs a
    known context window of at least 32K tokens: 422 `context_window_too_small` otherwise; an
    unknown window is allowed with a `warnings` entry. Unticking a role needs a replacement in each
    Workspace where the model is the default or only model for it: 409 `replacement_required`
    lists them, `replacement_not_allowed` and `embedding_model_indexed` as for removal; other
    Workspaces just lose it."""
    current = providers.get_model(model_id)
    removed = tuple(role for role in ROLES if role in set(current.roles) - set(body.roles))
    if removed:
        release_roles(
            request.app.state.engine, model_id, removed, _replacements(body.workspace_replacements)
        )
    return _model_out(providers.set_model_roles(model_id, body.roles))


@router.delete("/models/{model_id}", operation_id="deleteLlmModel", status_code=204)
def delete_model(
    model_id: uuid.UUID, request: Request, _admin: LlmAdmin, providers: ProviderServiceDep
) -> Response:
    """Remove an unused model (admins only; Workspaces holding it only as a non-default extra
    simply lose it). 409 `model_in_use` when a Workspace depends on it:
    remove it with `POST /models/{id}/removal` and a replacement."""
    if model_usage(request.app.state.engine, model_id).in_use:
        raise ApiError(
            409,
            "model_in_use",
            "This model is in use: choose a replacement for each role it fills.",
        )
    providers.delete_model(model_id)
    return Response(status_code=204)


class LlmModelUsageWorkspace(BaseModel):
    id: uuid.UUID
    name: str
    roles: list[ModelRole] = Field(
        description="The roles in this Workspace that need a replacement."
    )
    internal_only: bool = Field(default=False, description="Only internal models are accepted.")


class LlmModelUsage(BaseModel):
    in_use: bool
    roles: list[ModelRole] = Field(description="Every role the model fills, in any Workspace.")
    workspaces: list[LlmModelUsageWorkspace]
    indexed_workspaces: list[LlmModelUsageWorkspace] = Field(
        description="Workspaces with documents indexed by this model: they block its removal."
    )


def _usage_ws(refs: list[WorkspaceRef]) -> list[LlmModelUsageWorkspace]:
    return [
        LlmModelUsageWorkspace(id=w.id, name=w.name, roles=w.roles, internal_only=w.internal_only)
        for w in refs
    ]  # type: ignore[arg-type]


@router.get("/models/{model_id}/usage", operation_id="getLlmModelUsage")
def get_model_usage(model_id: uuid.UUID, request: Request, _admin: LlmAdmin) -> LlmModelUsage:
    """Where a model is used, for the removal popup (admins only)."""
    usage = model_usage(request.app.state.engine, model_id)
    return LlmModelUsage(
        in_use=usage.in_use,
        roles=usage.roles,  # type: ignore[arg-type]
        workspaces=_usage_ws(usage.workspaces),
        indexed_workspaces=_usage_ws(usage.indexed_workspaces),
    )


class LlmModelRemoval(BaseModel):
    workspace_replacements: list[LlmWorkspaceReplacement] = Field(
        default_factory=list,
        description="Per Workspace and role, the replacement where the model is the "
        "Workspace's default or only model for it (an internal-only Workspace takes only "
        "internal models).",
    )


def _replacements(items: list[LlmWorkspaceReplacement]) -> list[WorkspaceReplacement]:
    return [
        WorkspaceReplacement(
            w.workspace_id, w.agent_model_id, w.light_model_id, w.embedding_model_id
        )
        for w in items
    ]


@router.post("/models/{model_id}/removal", operation_id="removeLlmModel", status_code=204)
def remove_model_in_use(
    model_id: uuid.UUID, body: LlmModelRemoval, request: Request, _admin: LlmAdmin
) -> Response:
    """Remove a model with a replacement per Workspace and role where it is the default or
    only model, applied atomically (admins only). 409 `replacement_required` and
    `replacement_not_allowed` (naming the Workspaces), 409 `embedding_model_indexed`
    (naming the Workspaces whose documents are indexed with it)."""
    remove_model(
        request.app.state.engine,
        model_id,
        workspace_replacements=_replacements(body.workspace_replacements),
    )
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


class LlmWorkspaceBudget(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str | None = Field(description="Null if the Workspace no longer exists.")
    monthly_token_budget: int


class LlmCostBudget(BaseModel):
    currency: str
    amount: Decimal


class LlmWorkspaceCostBudget(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str | None = Field(description="Null if the Workspace no longer exists.")
    budget: LlmCostBudget


class LlmBudgets(BaseModel):
    installation_monthly_token_budget: int | None = Field(description="Null: unlimited.")
    workspaces: list[LlmWorkspaceBudget]
    installation_monthly_cost_budget: LlmCostBudget | None = Field(description="Null: unlimited.")
    workspace_cost_budgets: list[LlmWorkspaceCostBudget]


class LlmCostBudgetRequest(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. `USD`.")
    amount: Decimal = Field(
        ge=0,
        le=MAX_COST_BUDGET,
        description="Per calendar month (UTC). Only calls priced in `currency` count.",
    )


class LlmBudgetRequest(BaseModel):
    monthly_token_budget: int | None = Field(
        ge=0,
        le=MAX_BUDGET,
        description="Tokens per calendar month (UTC). Null removes the installation's limit.",
    )


class LlmWorkspaceBudgetRequest(BaseModel):
    monthly_token_budget: int = Field(ge=0, le=MAX_BUDGET)


class LlmCostAmount(BaseModel):
    currency: str
    amount: Decimal


class LlmCost(BaseModel):
    amounts: list[LlmCostAmount] = Field(
        description="Cost of the calls whose model has a price, per currency."
    )
    unpriced_calls: int = Field(
        description="Calls whose model has no price: their cost is unknown, not zero."
    )


class LlmUsageTotals(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int
    estimated_calls: int = Field(description="Calls whose tokens DAWAM estimated.")
    cost: LlmCost


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


def _cost_out(cost: Cost) -> LlmCost:
    return LlmCost(
        amounts=[
            LlmCostAmount(currency=currency, amount=amount.quantize(Decimal("0.000001")))
            for currency, amount in sorted(cost.amounts.items())
        ],
        unpriced_calls=cost.unpriced_calls,
    )


def _totals_out(totals: UsageTotals) -> LlmUsageTotals:
    return LlmUsageTotals(
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        total_tokens=totals.total_tokens,
        calls=totals.calls,
        estimated_calls=totals.estimated_calls,
        cost=_cost_out(totals.cost),
    )


def _cost_budget_out(budget: CostBudget | None) -> LlmCostBudget | None:
    return None if budget is None else LlmCostBudget(currency=budget.currency, amount=budget.amount)


def _budgets_out(roles: RoleService, workspaces: WorkspaceService) -> LlmBudgets:
    budgets = roles.budgets()
    names = workspaces.names_by_id(
        [b.workspace_id for b in budgets.workspaces]
        + [b.workspace_id for b in budgets.workspace_costs]
    )
    return LlmBudgets(
        installation_monthly_token_budget=budgets.installation,
        installation_monthly_cost_budget=_cost_budget_out(budgets.installation_cost),
        workspace_cost_budgets=[
            LlmWorkspaceCostBudget(
                workspace_id=b.workspace_id,
                workspace_name=names.get(b.workspace_id),
                budget=LlmCostBudget(currency=b.budget.currency, amount=b.budget.amount),
            )
            for b in budgets.workspace_costs
        ],
        workspaces=[
            LlmWorkspaceBudget(
                workspace_id=b.workspace_id,
                workspace_name=names.get(b.workspace_id),
                monthly_token_budget=b.monthly_token_budget,
            )
            for b in budgets.workspaces
        ],
    )


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


@router.put("/budgets/installation/cost", operation_id="setLlmInstallationCostBudget")
def set_installation_cost_budget(
    body: LlmCostBudgetRequest,
    _admin: LlmAdmin,
    roles: RoleServiceDep,
    workspaces: WorkspaceNamesDep,
) -> LlmBudgets:
    """Set the installation's monthly budget in currency (admins only), beside its token
    budget: whichever is reached first stops AI calls (429 `cost_budget_exhausted` for
    this one). Cost comes from the model price table, so models without a price, or
    priced in another currency, don't count."""
    roles.set_installation_cost_budget(CostBudget(body.currency, body.amount))
    return _budgets_out(roles, workspaces)


@router.delete(
    "/budgets/installation/cost", operation_id="clearLlmInstallationCostBudget", status_code=204
)
def clear_installation_cost_budget(_admin: LlmAdmin, roles: RoleServiceDep) -> Response:
    """Remove the installation's currency budget (admins only)."""
    roles.set_installation_cost_budget(None)
    return Response(status_code=204)


@router.put("/budgets/workspaces/{workspace_id}/cost", operation_id="setLlmWorkspaceCostBudget")
def set_workspace_cost_budget(
    workspace_id: uuid.UUID,
    body: LlmCostBudgetRequest,
    _admin: LlmAdmin,
    roles: RoleServiceDep,
    workspaces: WorkspaceNamesDep,
) -> LlmBudgets:
    """Set a Workspace's own monthly budget in currency (admins only). 404 `workspace_not_found`."""
    if workspace_id not in workspaces.names_by_id([workspace_id]):
        raise ApiError(404, "workspace_not_found", "Workspace not found.")
    roles.set_workspace_cost_budget(workspace_id, CostBudget(body.currency, body.amount))
    return _budgets_out(roles, workspaces)


@router.delete(
    "/budgets/workspaces/{workspace_id}/cost",
    operation_id="clearLlmWorkspaceCostBudget",
    status_code=204,
)
def clear_workspace_cost_budget(
    workspace_id: uuid.UUID, _admin: LlmAdmin, roles: RoleServiceDep
) -> Response:
    """Remove a Workspace's own currency budget (admins only)."""
    roles.clear_workspace_cost_budget(workspace_id)
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


PRICE_MAX = Decimal(10**9)


class LlmModelPriceRequest(BaseModel):
    model_name: str = Field(
        min_length=1,
        max_length=MODEL_NAME_MAX_LENGTH,
        description="The model's name, as usage records carry it.",
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. `USD`.")
    input_per_million: Decimal = Field(ge=0, le=PRICE_MAX, description="Per million input tokens.")
    output_per_million: Decimal = Field(
        ge=0, le=PRICE_MAX, description="Per million output tokens."
    )
    cached_input_per_million: Decimal = Field(
        ge=0,
        le=PRICE_MAX,
        description="Per million input tokens served from the provider's prompt cache.",
    )


class LlmModelPrice(BaseModel):
    model_name: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal


class LlmModelPrices(BaseModel):
    prices: list[LlmModelPrice]


def _prices_out(roles: RoleService) -> LlmModelPrices:
    return LlmModelPrices(
        prices=[
            LlmModelPrice(
                model_name=p.model_name,
                currency=p.currency,
                input_per_million=p.input,
                output_per_million=p.output,
                cached_input_per_million=p.cached_input,
            )
            for p in roles.prices()
        ]
    )


@router.get("/prices", operation_id="getLlmModelPrices")
def get_prices(_admin: LlmAdmin, roles: RoleServiceDep) -> LlmModelPrices:
    """The model price table (admins only). A model without a row has an unknown cost."""
    return _prices_out(roles)


@router.put("/prices", operation_id="setLlmModelPrice")
def set_price(
    body: LlmModelPriceRequest, _admin: LlmAdmin, roles: RoleServiceDep
) -> LlmModelPrices:
    """Add or replace one model's price (admins only). Usage cost uses it from now on,
    for past calls too: cost is computed from recorded tokens when usage is read."""
    roles.set_price(
        ModelPrice(
            body.model_name,
            body.currency,
            body.input_per_million,
            body.output_per_million,
            body.cached_input_per_million,
        )
    )
    return _prices_out(roles)


@router.delete("/prices", operation_id="clearLlmModelPrice", status_code=204)
def clear_price(
    model_name: Annotated[str, Query(min_length=1, max_length=MODEL_NAME_MAX_LENGTH)],
    _admin: LlmAdmin,
    roles: RoleServiceDep,
) -> Response:
    """Remove a model's price (admins only): its cost becomes unknown."""
    roles.clear_price(model_name)
    return Response(status_code=204)
