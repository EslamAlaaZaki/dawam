"""``.../systems/{system_id}/pii-scans``: the value-based PII scan (spec stories 131, 132).

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


class PiiScanStarted(BaseModel):
    job_id: uuid.UUID = Field(
        description="The `pii_scan` job: follow its status, progress and log at "
        "`GET /jobs/{job_id}`."
    )
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]


@router.post("/pii-scans", operation_id="startPiiScan", status_code=202)
def start_pii_scan(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: PiiScanRequest,
    user: CurrentUser,
    scans: PiiScanServiceDep,
) -> PiiScanStarted:
    """Scan the selected tables' sampled values for PII, as a background job (owners and
    editors; live Connection only). Values are tested in memory and discarded: only the
    match ratio is kept, as the evidence of a `suggested` finding. 409
    `connection_missing` without a Connection; 422 for no tables or a bad sample size."""
    job = scans.start_scan(
        user, workspace_id, system_id, body.table_ids, sample_size=body.sample_size
    )
    return PiiScanStarted(job_id=job.id, status=job.status)  # type: ignore[arg-type]
