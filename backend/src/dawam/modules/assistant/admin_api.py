"""``/api/v1/admin/assistant/runs``: the run explorer, for admins only. Prompts, answers, tool
arguments and feedback comments are shown redacted."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import AuthService, CurrentUser, User
from dawam.modules.llm import ProviderService, RoleService, WorkspaceAiService
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.errors import ApiError

from .run_explorer import RunCost, RunDetail, RunExplorer, RunStep, RunSummary

router = APIRouter(prefix="/admin/assistant", tags=["assistant"])


def run_reviewer(user: CurrentUser) -> User:
    if not can(user, Action.VIEW_ASSISTANT_RUNS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


RunReviewer = Annotated[User, Depends(run_reviewer)]


def run_explorer(request: Request) -> RunExplorer:
    state = request.app.state
    clock = state.services.clock
    factory = getattr(state.services, "llm_adapters", None)
    providers = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        **({"adapters": factory} if factory else {}),
    )
    workspaces = WorkspaceService(state.engine, clock=clock)
    return RunExplorer(
        state.engine,
        workspaces=workspaces,
        roles=RoleService(state.engine, providers=providers, clock=clock),
        ai=WorkspaceAiService(state.engine, workspaces=workspaces, clock=clock),
        users=AuthService(state.engine, state.settings, clock=clock).get_user,
    )


RunExplorerDep = Annotated[RunExplorer, Depends(run_explorer)]


class RunCostOut(BaseModel):
    amount: Decimal
    currency: str = Field(description="ISO 4217 code of the model's price.")


class RunSummaryOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str | None
    conversation_id: uuid.UUID
    member_id: uuid.UUID | None
    member_email: str | None
    status: str
    prompt_version: str | None = Field(description="Id of the prompts the run was given.")
    model_name: str | None
    input_tokens: int
    output_tokens: int
    duration_ms: int
    cost: RunCostOut | None = Field(description="Null when the model has no price.")
    tool_call_count: int
    thumbs_up: int
    thumbs_down: int
    started_at: datetime


class RunListOut(BaseModel):
    items: list[RunSummaryOut]


class RunStepOut(BaseModel):
    kind: str = Field(
        description="`model` (the run's model calls, summed), `tool` (one tool call) or `error`."
    )
    name: str
    status: str
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost: RunCostOut | None
    prompt: str | None = Field(description="The member's question as sent, redacted.")
    output: str | None = Field(description="The answer, redacted.")
    arguments: dict[str, Any] | None
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None


class RunFeedbackOut(BaseModel):
    member_email: str | None
    rating: str
    comment: str | None
    updated_at: datetime


class RunDetailOut(BaseModel):
    run: RunSummaryOut
    finished_at: datetime | None
    timeline: list[RunStepOut] = Field(description="The run's stored steps, in order.")
    feedback: list[RunFeedbackOut]


def _cost(cost: RunCost | None) -> RunCostOut | None:
    return RunCostOut(amount=cost.amount, currency=cost.currency) if cost else None


def _summary(s: RunSummary) -> RunSummaryOut:
    return RunSummaryOut(**{**s.__dict__, "cost": _cost(s.cost)})


def _step(s: RunStep) -> RunStepOut:
    return RunStepOut(**{**s.__dict__, "cost": _cost(s.cost)})


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


@router.get("/runs", operation_id="listAssistantRuns")
def list_runs(
    admin: RunReviewer,
    explorer: RunExplorerDep,
    workspace_id: uuid.UUID | None = None,
    member_id: Annotated[
        uuid.UUID | None, Query(description="Only runs answering this member's questions.")
    ] = None,
    status: Annotated[
        str | None,
        Query(description="`running`, `completed`, `tool_limit`, `cancelled` or `failed`."),
    ] = None,
    started_from: Annotated[
        datetime | None, Query(description="Only runs started at or after this time.")
    ] = None,
    started_to: Annotated[
        datetime | None, Query(description="Only runs started before this time.")
    ] = None,
) -> RunListOut:
    """Assistant runs across all Workspaces, newest first, at most 100 (admins only). A
    time without a time zone is UTC."""
    runs = explorer.list_runs(
        admin,
        workspace_id=workspace_id,
        member_id=member_id,
        status=status,
        started_from=_utc(started_from),
        started_to=_utc(started_to),
    )
    return RunListOut(items=[_summary(r) for r in runs])


@router.get("/runs/{run_id}", operation_id="getAssistantRun")
def get_run(run_id: uuid.UUID, admin: RunReviewer, explorer: RunExplorerDep) -> RunDetailOut:
    """One run with its step timeline (model calls, tool calls, errors), redacted prompt and
    answer, prompt version and every member's feedback (admins only); 404 if unknown."""
    detail: RunDetail = explorer.get_run(admin, run_id)
    return RunDetailOut(
        run=_summary(detail.summary),
        finished_at=detail.finished_at,
        timeline=[_step(s) for s in detail.timeline],
        feedback=[RunFeedbackOut(**f.__dict__) for f in detail.feedback],
    )
