"""``/api/v1/admin/ai/jobs``: the background AI explorer (#631), for admins only. Each row is
a background job's model calls, or a Workspace's document searches of one day; no prompt or
output is stored or returned."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import AuthService, CurrentUser, User
from dawam.modules.jobs import JobRunner, JobService
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.errors import ApiError

from .ai_jobs import AiCost, AiJobExplorer, AiJobSummary
from .role_service import RoleService
from .service import ProviderService

router = APIRouter(prefix="/admin/ai", tags=["llm"])


def ai_reviewer(user: CurrentUser) -> User:
    if not can(user, Action.VIEW_ASSISTANT_RUNS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


AiReviewer = Annotated[User, Depends(ai_reviewer)]


def ai_job_explorer(request: Request) -> AiJobExplorer:
    state = request.app.state
    clock = state.services.clock
    factory = getattr(state.services, "llm_adapters", None)
    providers = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        **({"adapters": factory} if factory else {}),
    )
    runner: JobRunner = state.services.jobs
    return AiJobExplorer(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        roles=RoleService(state.engine, providers=providers, clock=clock),
        jobs=JobService(state.engine, runner=runner, clock=clock).by_ids,
        users=AuthService(state.engine, state.settings, clock=clock).get_user,
        clock=clock,
    )


AiJobExplorerDep = Annotated[AiJobExplorer, Depends(ai_job_explorer)]


class AiCostOut(BaseModel):
    amount: Decimal
    currency: str = Field(description="ISO 4217 code of the models' price.")


class AiJobOut(BaseModel):
    key: str = Field(
        description="The job's id, or `search:<workspace id>:<YYYY-MM-DD>` for a Workspace's "
        "searches of one UTC day."
    )
    feature: str = Field(
        description="`catalog_suggestions`, `pii_scan`, `relationships`, `kpi_suggestions`, "
        "`file_indexing`, `search` or `unknown`."
    )
    target: str | None = Field(description="What the job worked on; null for searches.")
    job_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    workspace_name: str | None
    started_by: str = Field(
        description="The member's email, `Scheduled`, or for searches how many members searched."
    )
    member_count: int | None = Field(description="For searches: the members who searched.")
    started_at: datetime
    status: str = Field(
        description="`running`, `completed`, `partial` (some calls failed) or `failed`."
    )
    calls: int
    failed_calls: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cost: AiCostOut | None = Field(
        description="Null when a model has no price (unknown, not zero)."
    )
    models: list[str]


class AiJobListOut(BaseModel):
    items: list[AiJobOut]


class ModelCallsOut(BaseModel):
    role: str
    model_name: str
    calls: int
    failed_calls: int
    avg_latency_ms: int | None
    input_tokens: int
    output_tokens: int
    cost: AiCostOut | None = Field(description="Null when the model has no price.")


class FailedCallOut(BaseModel):
    at: datetime
    error_code: str | None
    role: str
    model_name: str


class AiJobDetailOut(BaseModel):
    job: AiJobOut
    models: list[ModelCallsOut] = Field(description="The calls per role and model.")
    failed_calls: list[FailedCallOut] = Field(description="Oldest first, at most 100.")


def _cost(cost: AiCost | None) -> AiCostOut | None:
    return AiCostOut(amount=cost.amount, currency=cost.currency) if cost else None


def _job(s: AiJobSummary) -> AiJobOut:
    return AiJobOut(**{**s.__dict__, "cost": _cost(s.cost)})


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


@router.get("/jobs", operation_id="listAiJobs")
def list_ai_jobs(
    admin: AiReviewer,
    explorer: AiJobExplorerDep,
    workspace_id: uuid.UUID | None = None,
    feature: Annotated[
        str | None,
        Query(
            description="`catalog_suggestions`, `pii_scan`, `relationships`, "
            "`kpi_suggestions`, `file_indexing`, `search` or `unknown`."
        ),
    ] = None,
    status: Annotated[
        str | None, Query(description="`running`, `completed`, `partial` or `failed`.")
    ] = None,
    started_from: Annotated[
        datetime | None, Query(description="Only those started at or after this time.")
    ] = None,
    started_to: Annotated[
        datetime | None, Query(description="Only those started before this time.")
    ] = None,
) -> AiJobListOut:
    """Model calls made outside the assistant, newest first, at most 100 (admins only): one
    row per background job, and one per Workspace per day of document searches. A time
    without a time zone is UTC."""
    jobs = explorer.list_jobs(
        admin,
        workspace_id=workspace_id,
        feature=feature,
        status=status,
        started_from=_utc(started_from),
        started_to=_utc(started_to),
    )
    return AiJobListOut(items=[_job(j) for j in jobs])


@router.get("/jobs/{key}", operation_id="getAiJob")
def get_ai_job(key: str, admin: AiReviewer, explorer: AiJobExplorerDep) -> AiJobDetailOut:
    """One job (or day of searches) with its calls per role and model and its failed calls
    (admins only); 404 if it made no model call."""
    detail = explorer.get_job(admin, key)
    return AiJobDetailOut(
        job=_job(detail.summary),
        models=[ModelCallsOut(**{**m.__dict__, "cost": _cost(m.cost)}) for m in detail.models],
        failed_calls=[FailedCallOut(**f.__dict__) for f in detail.failed_calls],
    )
