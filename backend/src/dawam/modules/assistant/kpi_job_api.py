"""``.../systems/{system_id}/kpi-suggestions``: AI KPI suggestions for one Source System, and
``.../data-warehouse/kpi-suggestions``: cross-system ones for the Data Warehouse, as a
background job (spec §6.13, story 75).

Handlers only translate HTTP to ``KpiSuggestionJobs`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.jobs import Job

from .api import AssistantServiceDep

router = APIRouter(
    prefix="/workspaces/{workspace_id}/systems/{system_id}/kpi-suggestions", tags=["kpis"]
)
dw_router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/kpi-suggestions", tags=["kpis"]
)


class KpiSuggestionJob(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    error: str | None = Field(description="Why the job failed; null otherwise.")
    created: int | None = Field(
        description="New suggested KPIs; null until the job has succeeded. Zero is a normal "
        "outcome: nothing differs from the KPIs already there."
    )
    dropped: int | None = Field(
        description="Suggestions dropped because of the tables they were based on: outside "
        "the Source System or, for the Data Warehouse, one Source System's only; null until "
        "the job has succeeded."
    )
    skipped: int | None = Field(
        description="Suggestions skipped because a KPI of that name exists there (any review "
        "state); null until the job has succeeded."
    )
    created_at: datetime
    finished_at: datetime | None


class LatestKpiSuggestionJob(BaseModel):
    job: KpiSuggestionJob | None = Field(description="The newest job; null if none ran yet.")


class LatestDwKpiSuggestionJob(BaseModel):
    job: KpiSuggestionJob | None = Field(description="The newest job; null if none ran yet.")
    available: bool = Field(
        description="Whether a job may start: the Workspace has at least two Source Systems "
        "or a DW model."
    )
    source_systems: int = Field(description="The Workspace's Source Systems, deleted ones not.")
    dw_model: bool = Field(description="Whether the DW model exists (Core or Mart has tables).")


def _out(job: Job) -> KpiSuggestionJob:
    result = job.result or {}

    def count(key: str) -> int | None:
        value = result.get(key)
        return value if isinstance(value, int) and job.status == "succeeded" else None

    return KpiSuggestionJob(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]  # the jobs table's CHECK values
        error=job.error,
        created=count("created"),
        dropped=count("dropped"),
        skipped=count("skipped"),
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


@router.post("", operation_id="startKpiSuggestions", status_code=202)
def start_kpi_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    assistant: AssistantServiceDep,
) -> KpiSuggestionJob:
    """Suggest KPIs for this Source System with the AI, as a background job (owners and
    editors). The AI reads this system only and sees every KPI under it in every review
    state; suggestions land in the Suggested tab. 409 `kpi_suggestions_running` while a job
    runs for this system already."""
    return _out(assistant.kpi_suggestions().start(user, workspace_id, system_id))


@router.get("", operation_id="getKpiSuggestions")
def get_kpi_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    assistant: AssistantServiceDep,
) -> LatestKpiSuggestionJob:
    """The newest KPI suggestion job of this Source System, with its outcome once it has
    succeeded (owners and editors)."""
    job = assistant.kpi_suggestions().latest(user, workspace_id, system_id)
    return LatestKpiSuggestionJob(job=_out(job) if job is not None else None)


@dw_router.post("", operation_id="startDwKpiSuggestions", status_code=202)
def start_dw_kpi_suggestions(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    assistant: AssistantServiceDep,
) -> KpiSuggestionJob:
    """Suggest cross-system KPIs for the Data Warehouse with the AI, as a background job
    (owners and editors): only KPIs that need two or more Source Systems or the DW model. The
    AI sees every KPI in the Workspace in every review state; suggestions land in the Data
    Warehouse's Suggested tab. 409 `kpi_suggestions_unavailable` until the Workspace has two
    Source Systems or a DW model; 409 `kpi_suggestions_running` while a job runs already."""
    return _out(assistant.kpi_suggestions().start_dw(user, workspace_id))


@dw_router.get("", operation_id="getDwKpiSuggestions")
def get_dw_kpi_suggestions(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    assistant: AssistantServiceDep,
) -> LatestDwKpiSuggestionJob:
    """The newest cross-system KPI suggestion job, with its outcome once it has succeeded,
    and whether one may start (owners and editors)."""
    job, readiness = assistant.kpi_suggestions().latest_dw(user, workspace_id)
    return LatestDwKpiSuggestionJob(
        job=_out(job) if job is not None else None,
        available=readiness.ready,
        source_systems=readiness.source_systems,
        dw_model=readiness.dw_model,
    )
