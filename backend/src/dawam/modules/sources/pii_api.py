"""``.../systems/{system_id}/pii-findings`` and ``.../pii-columns``: the PII folder (spec
stories 133-135).

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
from dawam.platform.errors import ApiError

from .pii_service import PiiService
from .profiling_api import ProfilingServiceDep
from .snapshot_api import PiiCategory

router = APIRouter(tags=["sources"])


def pii_service(request: Request) -> PiiService:
    state = request.app.state
    clock = state.services.clock
    return PiiService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        clock=clock,
        hashed_mappings=getattr(state, "pii_hashed_mappings", None),
    )


PiiServiceDep = Annotated[PiiService, Depends(pii_service)]

PiiStatus = Literal["suggested", "accepted", "rejected"]
PiiTab = Literal["pii", "suggested", "accepted", "rejected"]


class PiiFinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    rule: str = Field(description="The rule that fired, e.g. `national_id`.")
    category: PiiCategory
    confidence: float = Field(description="0 to 1; a name match alone is at least 0.5.")
    evidence: str = Field(description="Why it matched. Never a data value.")
    status: PiiStatus = Field(description="`suggested` findings are the review queue.")
    is_sensitive: bool = Field(description="The column's own sensitive flag.")
    is_protected: bool = Field(
        description="`is_sensitive` or a `suggested`/`accepted` finding on the column."
    )
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


class PiiFindingPage(BaseModel):
    items: list[PiiFinding] = Field(description="Most confident first.")


class PiiRuleMatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: Literal["name", "value"] = Field(
        description="`name`: a name scan; `value`: a value scan or a query's values."
    )
    rule: str = Field(description="`national_id`, `saudi_national_id`, `custom:<name>`, ...")
    category: PiiCategory
    confidence: float
    evidence: str = Field(description="Why it matched: names and ratios, never a value.")
    status: PiiStatus


class PiiColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Column.")
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    data_type: str | None
    source_status: Literal["present", "source_removed", "out_of_scope"] = Field(
        description="Not `present`: the column is gone from the source; the decision stays."
    )
    state: Literal["accepted", "suggested", "rejected"]
    category: PiiCategory | None = Field(
        description="The column's PII category once accepted; else its most confident rule's."
    )
    is_sensitive: bool
    rules: list[PiiRuleMatch] = Field(description="Every rule that matched, most confident first.")
    manual: bool = Field(description="Added as PII by hand (a Manual line, no rule).")
    decision: Literal["accepted", "added", "rejected", "removed"] | None = Field(
        description="The latest decision that put the column in its tab."
    )
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    rejected_before_by: uuid.UUID | None = Field(
        description="A suggested column rejected before, back after a later scan."
    )
    rejected_before_at: datetime | None
    hashed_mapping: str | None = Field(
        description="The staging mapping that hashes the column; PII cannot be removed then."
    )
    table_profiled_at: datetime | None


class PiiTabCounts(BaseModel):
    pii: int
    suggested: int
    accepted: int
    rejected: int


class PiiColumnPage(BaseModel):
    items: list[PiiColumn]
    counts: PiiTabCounts
    can_profile: bool = Field(description="A live Connection: a table can be profiled again.")


class PiiCategoryRequest(BaseModel):
    category: PiiCategory


class AddPiiColumnRequest(BaseModel):
    column_id: uuid.UUID
    category: PiiCategory


class RemovePiiRequest(BaseModel):
    profile_again: bool = Field(
        default=False, description="Also profile the column's table again, as a background job."
    )


class PiiRemoved(BaseModel):
    column: PiiColumn | None = Field(
        description="The column, now rejected; `null` for a column added by hand only."
    )
    profile_job_id: uuid.UUID | None = Field(
        description="The profiling job started for the table, if asked and possible."
    )


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
    """The Source System's PII findings, one per column and rule (owners and editors)."""
    items = findings.list_findings(user, workspace_id, system_id, status=status)
    return PiiFindingPage(items=[PiiFinding.model_validate(i) for i in items])


@router.get("/pii-columns", operation_id="listPiiColumns")
def list_pii_columns(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    pii: PiiServiceDep,
    tab: Annotated[PiiTab, Query(description="The PII folder's tab.")] = "pii",
) -> PiiColumnPage:
    """One tab of the PII folder, one row per column, with every tab's count. Any member
    reads the `pii` tab (names and ratios only, never values); the other tabs are for
    owners and editors."""
    page = pii.list_columns(user, workspace_id, system_id, tab=tab)
    return PiiColumnPage(
        items=[PiiColumn.model_validate(c) for c in page.items],
        counts=PiiTabCounts(**page.counts),
        can_profile=page.can_profile,
    )


@router.post("/pii-columns", operation_id="addPiiColumn")
def add_pii_column(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: AddPiiColumnRequest,
    user: CurrentUser,
    pii: PiiServiceDep,
) -> PiiColumn:
    """Add a column as PII by hand (owners and editors): flagged sensitive with the
    category, no finding. A rejected column comes back accepted. Audited."""
    column = pii.add(user, workspace_id, system_id, body.column_id, category=body.category)
    return PiiColumn.model_validate(column)


@router.post("/pii-columns/{column_id}/accept", operation_id="acceptPiiColumn")
def accept_pii_column(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    column_id: uuid.UUID,
    body: PiiCategoryRequest,
    user: CurrentUser,
    pii: PiiServiceDep,
) -> PiiColumn:
    """Accept a suggested column as PII (owners and editors): every finding on it is
    accepted and the column becomes sensitive with the category. Audited."""
    column = pii.accept(user, workspace_id, system_id, column_id, category=body.category)
    return PiiColumn.model_validate(column)


@router.post("/pii-columns/{column_id}/reject", operation_id="rejectPiiColumn")
def reject_pii_column(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    column_id: uuid.UUID,
    user: CurrentUser,
    pii: PiiServiceDep,
) -> PiiColumn:
    """Reject a suggested column (owners and editors): every finding on it is rejected.
    Audited."""
    return PiiColumn.model_validate(pii.reject(user, workspace_id, system_id, column_id))


@router.post("/pii-columns/{column_id}/accept-rejected", operation_id="acceptRejectedPiiColumn")
def accept_rejected_pii_column(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    column_id: uuid.UUID,
    body: PiiCategoryRequest,
    user: CurrentUser,
    pii: PiiServiceDep,
) -> PiiColumn:
    """Accept a rejected column as PII after all (owners and editors). Audited."""
    column = pii.accept_rejected(user, workspace_id, system_id, column_id, category=body.category)
    return PiiColumn.model_validate(column)


@router.post("/pii-columns/{column_id}/remove", operation_id="removePiiColumn")
def remove_pii_column(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    column_id: uuid.UUID,
    body: RemovePiiRequest,
    user: CurrentUser,
    pii: PiiServiceDep,
    profiling: ProfilingServiceDep,
) -> PiiRemoved:
    """Remove a column's PII (owners and editors): every finding is rejected and the
    sensitive flag and category are cleared. 409 `pii_column_hashed` while its staging
    mapping is hashed. With `profile_again`, the column's table is profiled again. Audited."""
    removal = pii.remove(user, workspace_id, system_id, column_id)
    job_id = None
    if body.profile_again:
        try:
            job_id = profiling.start_profiling(user, workspace_id, system_id, [removal.table_id]).id
        except ApiError:  # no live Connection any more: the removal stands
            job_id = None
    column = removal.column
    return PiiRemoved(
        column=PiiColumn.model_validate(column) if column is not None else None,
        profile_job_id=job_id,
    )
