"""Job endpoints: ``GET /jobs/{job_id}``, ``POST /jobs/{job_id}/cancel`` and
``GET /workspaces/{workspace_id}/jobs``.

Handlers only translate HTTP to ``JobService`` calls; the service authorizes every call
through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .service import Job as JobView
from .service import JobRunner, JobService

router = APIRouter(tags=["jobs"])


def job_service(request: Request) -> JobService:
    state = request.app.state
    runner: JobRunner = state.services.jobs
    return JobService(state.engine, runner=runner, clock=state.services.clock)


JobServiceDep = Annotated[JobService, Depends(job_service)]


class Job(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    title: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int = Field(description="Percent done, 0 to 100.")
    log: str = Field(description="The job's log, one timestamped line per entry.")
    error: str | None = Field(description="Why the job failed; null otherwise.")
    created_by: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobPage(BaseModel):
    items: list[Job]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


def _out(job: JobView) -> Job:
    return Job.model_validate(job)


@router.get("/jobs/{job_id}", operation_id="getJob")
def get_job(job_id: uuid.UUID, user: CurrentUser, jobs: JobServiceDep) -> Job:
    """A job's status, progress and log (any member of its Workspace)."""
    return _out(jobs.get(user, job_id))


@router.post("/jobs/{job_id}/cancel", operation_id="cancelJob")
def cancel_job(job_id: uuid.UUID, user: CurrentUser, jobs: JobServiceDep) -> Job:
    """Cancel a queued or running job. Its creator may; so may an owner of its
    Workspace. 409 `job_finished` if it has ended."""
    return _out(jobs.cancel(user, job_id))


@router.get("/workspaces/{workspace_id}/jobs", operation_id="listJobs")
def list_jobs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    jobs: JobServiceDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> JobPage:
    """The Workspace's jobs, newest first (any member)."""
    page = jobs.list(user, workspace_id, limit=limit, cursor=cursor)
    return JobPage(items=[_out(j) for j in page.items], next_cursor=page.next_cursor)
