"""``.../systems/{system_id}/pii-scans``: Detect PII, the value-based PII scan and the AI
PII detection side by side (spec stories 131, 132).

Handlers only translate HTTP to ``PiiScanService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .internal.pii_scan import DEFAULT_SAMPLE_SIZE, MAX_SAMPLE_SIZE
from .pii_scan_service import PiiScanService

router = APIRouter(tags=["sources"])


def pii_scan_service(request: Request) -> PiiScanService:
    state = request.app.state
    clock = state.services.clock
    return PiiScanService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=state.services.jobs,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
    )


PiiScanServiceDep = Annotated[PiiScanService, Depends(pii_scan_service)]


class PiiScanRequest(BaseModel):
    table_ids: list[uuid.UUID] = Field(description="The Source Tables to scan.", max_length=500)
    sample_size: int = Field(
        default=DEFAULT_SAMPLE_SIZE,
        description=f"Rows sampled per table (1 to {MAX_SAMPLE_SIZE}); a column is tested on "
        "at most this many values.",
    )


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class PiiScanStarted(BaseModel):
    job_id: uuid.UUID | None = Field(
        description="The `pii_scan` value scan job: follow it at `GET /jobs/{job_id}`. "
        "`null` when the system has no Connection: the value scan is skipped."
    )
    status: JobStatus | None = Field(description="The value scan job's status.")
    ai_job_id: uuid.UUID = Field(
        description="The `pii_ai_detect` AI PII detection job, started at the same time and "
        "independent of the value scan."
    )
    ai_status: JobStatus = Field(description="The AI detection job's status.")


@router.post("/pii-scans", operation_id="startPiiScan", status_code=202)
def start_pii_scan(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: PiiScanRequest,
    user: CurrentUser,
    scans: PiiScanServiceDep,
) -> PiiScanStarted:
    """Detect PII in the selected tables (owners and editors), as two background jobs started
    together. The value scan (live Connection only; skipped without one) tests sampled values
    in memory and discards them: only the match ratio is kept. The AI detection reads the
    tables' metadata (names, types, keys, comments, existing findings and the Workspace's
    rules), and Documents when shared, never a data value. Both record `suggested`
    findings. 422 for no tables or a bad sample size."""
    started = scans.start_detection(
        user, workspace_id, system_id, body.table_ids, sample_size=body.sample_size
    )
    value = started.value_job
    return PiiScanStarted(
        job_id=value.id if value else None,
        status=value.status if value else None,  # type: ignore[arg-type]
        ai_job_id=started.ai_job.id,
        ai_status=started.ai_job.status,  # type: ignore[arg-type]
    )
