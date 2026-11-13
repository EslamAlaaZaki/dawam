"""``.../systems/{system_id}/kpi-suggestions``: AI KPI suggestions for one Source System, as
a background job (spec §6.13, story 75).

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


class KpiSuggestionJob(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    error: str | None = Field(description="Why the job failed; null otherwise.")
    created: int | None = Field(
        description="New suggested KPIs; null until the job has succeeded. Zero is a normal "
        "outcome: nothing differs from the KPIs already there."
    )
    dropped: int | None = Field(
        description="Suggestions dropped because they were based on tables outside the "
        "Source System; null until the job has succeeded."
    )
    skipped: int | None = Field(
        description="Suggestions skipped because a KPI of that name exists there (any review "
        "state); null until the job has succeeded."
    )
    created_at: datetime
    finished_at: datetime | None


class LatestKpiSuggestionJob(BaseModel):
    job: KpiSuggestionJob | None = Field(description="The newest job; null if none ran yet.")


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
