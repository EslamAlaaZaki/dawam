"""``.../systems/{system_id}/pii-findings``: the PII review queue (spec stories 133-135).

Handlers only translate HTTP to ``PiiService`` calls; the service authorizes every call
through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .pii_service import PiiService
from .snapshot_api import PiiCategory

router = APIRouter(tags=["sources"])


def pii_service(request: Request) -> PiiService:
    state = request.app.state
    clock = state.services.clock
    return PiiService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


PiiServiceDep = Annotated[PiiService, Depends(pii_service)]

PiiStatus = Literal["suggested", "confirmed", "dismissed"]


class PiiFinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    rule: str = Field(description="The name rule that fired, e.g. `national_id`.")
    category: PiiCategory
    confidence: float = Field(description="0 to 1; a name match alone is at least 0.5.")
    evidence: str = Field(description="Why it matched. Never a data value.")
    status: PiiStatus = Field(description="`suggested` findings are the review queue.")
    is_sensitive: bool = Field(description="The column's own sensitive flag.")
    is_protected: bool = Field(
        description="`is_sensitive` or a `suggested`/`confirmed` finding on the column."
    )
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


class PiiFindingPage(BaseModel):
    items: list[PiiFinding] = Field(description="Most confident first.")


@router.get("/pii-findings", operation_id="listPiiFindings")
def list_pii_findings(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    findings: PiiServiceDep,
    status: Annotated[
        PiiStatus | None,
        Query(description="Only findings in this state; the review queue is `suggested`."),
    ] = None,
) -> PiiFindingPage:
    """The Source System's PII findings (owners and editors)."""
    items = findings.list_findings(user, workspace_id, system_id, status=status)
    return PiiFindingPage(items=[PiiFinding.model_validate(i) for i in items])


@router.post("/pii-findings/{finding_id}/confirm", operation_id="confirmPiiFinding")
def confirm_pii_finding(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: CurrentUser,
    findings: PiiServiceDep,
) -> PiiFinding:
    """Confirm a finding (owners and editors): the column becomes sensitive and takes the
    finding's PII category. Audited."""
    return PiiFinding.model_validate(findings.confirm(user, workspace_id, system_id, finding_id))


@router.post("/pii-findings/{finding_id}/dismiss", operation_id="dismissPiiFinding")
def dismiss_pii_finding(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    finding_id: uuid.UUID,
    user: CurrentUser,
    findings: PiiServiceDep,
) -> PiiFinding:
    """Dismiss a finding (owners and editors). The column stays protected only if it is
    flagged sensitive. Audited."""
    return PiiFinding.model_validate(findings.dismiss(user, workspace_id, system_id, finding_id))
