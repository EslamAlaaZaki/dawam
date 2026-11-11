"""``.../systems/{system_id}/profiling`` and ``.../tables/{table_id}/profile``: profiling
(spec stories 55-57), and the Profiling page's overview and growth (story 57a).

Handlers only translate HTTP to ``ProfilingService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .internal.profiling_overview import FindingKind
from .profiling_overview_service import LeftOutReason, ProfilingOverviewService
from .profiling_service import (
    DEFAULT_ROW_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ROW_CAP,
    MAX_TABLES,
    MAX_TIMEOUT_SECONDS,
    ProfilingService,
)

router = APIRouter(tags=["sources"])


def profiling_service(request: Request) -> ProfilingService:
    state = request.app.state
    clock = state.services.clock
    return ProfilingService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=state.services.jobs,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
    )


ProfilingServiceDep = Annotated[ProfilingService, Depends(profiling_service)]


def profiling_overview_service(request: Request) -> ProfilingOverviewService:
    state = request.app.state
    return ProfilingOverviewService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=state.services.clock)
    )


OverviewServiceDep = Annotated[ProfilingOverviewService, Depends(profiling_overview_service)]


class StartProfilingRequest(BaseModel):
    table_ids: list[uuid.UUID] = Field(
        min_length=1, max_length=MAX_TABLES, description="The tables and views to profile."
    )
    row_cap: int = Field(
        default=DEFAULT_ROW_CAP,
        ge=1,
        le=MAX_ROW_CAP,
        description="At most this many rows of each table are read.",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_TIMEOUT_SECONDS,
        description="Each source query is stopped after this long.",
    )


class ProfilingStarted(BaseModel):
    job_id: uuid.UUID = Field(description="The `profile` job: follow it at `GET /jobs/{job_id}`.")
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]


class TopValue(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: str
    count: int


class ColumnProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_count: int = Field(description="Rows in the sample.")
    row_cap: int = Field(description="The sample cap of the run.")
    sampled: bool = Field(description="The sample hit its cap: the table may be larger.")
    null_pct: float = Field(description="0 to 100.")
    distinct_count: int | None = Field(description="Null for types the engine cannot compare.")
    min: str | None = Field(description="Always null for a Protected Column.")
    max: str | None = Field(description="Always null for a Protected Column.")
    avg_len: float | None = Field(description="Text columns only.")
    max_len: int | None = Field(description="Text columns only.")
    top_values: list[TopValue] | None = Field(
        description="Only while the table's top-N switch is on; never for a Protected Column."
    )
    patterns: list[str] = Field(description="Detected patterns such as `email` or `phone`.")
    profiled_at: datetime


class ColumnProfileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    column_id: uuid.UUID
    name: str
    data_type: str | None
    is_protected: bool
    profile: ColumnProfile | None = Field(description="Null until the column is profiled.")


class TableProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    db_schema: str
    name: str
    top_n_enabled: bool = Field(description="An owner's switch; off by default.")
    row_count: int | None = Field(description="Rows in the sample; null before profiling.")
    profiled_at: datetime | None
    columns: list[ColumnProfileView]


class OverviewFinding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    column_id: uuid.UUID
    column: str
    data_type: str
    kind: FindingKind = Field(description="The first rule the column's latest profile matches.")
    null_pct: float
    row_count: int = Field(description="Rows in the sample.")
    capped: bool = Field(
        description="The sample hit its cap: a `unique_not_key` column is unique only in the "
        "sampled rows."
    )
    value: str | None = Field(
        description="The one value of a `constant` column; never set for a Protected Column."
    )


class OverviewTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    db_schema: str
    name: str
    kind: Literal["table", "view"]
    row_estimate: int | None = Field(description="From the latest Snapshot.")
    column_count: int
    profiled_at: datetime | None = Field(description="Null until the table is profiled.")
    rows_sampled: int | None
    capped: bool = Field(description="The sample hit its cap: the table may be larger.")
    out_of_date: bool = Field(description="Profiled before the latest Snapshot.")
    findings: list[OverviewFinding]


class ProfilingOverview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    latest_snapshot_at: datetime | None = Field(description="Null before the first Snapshot.")
    tables: list[OverviewTable] = Field(description="The latest Snapshot's tables and views.")


class GrowthPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    taken_at: datetime
    row_estimate: int | None = Field(description="Null where the Snapshot has no estimate.")


class GrowthTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    db_schema: str
    name: str
    points: list[GrowthPoint] = Field(description="One per Snapshot, oldest first.")
    first: int
    latest: int
    change_pct: float | None = Field(description="Null when the first estimate is zero.")
    per_month: float = Field(description="Rows per month; from `dropped_at` on after a drop.")
    in_12_months: float = Field(description="Linear projection, never below zero.")
    dropped_at: datetime | None = Field(
        description="The Snapshot where the estimate last fell by more than a fifth."
    )


class LeftOutTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    db_schema: str
    name: str
    reason: LeftOutReason


class ProfilingGrowth(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshots: list[datetime] = Field(description="Every Snapshot's time, oldest first.")
    tables: list[GrowthTable] = Field(
        description="Fastest growing first; empty with fewer than two Snapshots."
    )
    left_out: list[LeftOutTable] = Field(description="Views, and tables with no growth to show.")
    total_first: int
    total_latest: int
    total_per_month: float


class TopNRequest(BaseModel):
    enabled: bool


@router.post("/profiling", operation_id="startProfiling", status_code=202)
def start_profiling(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: StartProfilingRequest,
    user: CurrentUser,
    profiling: ProfilingServiceDep,
) -> ProfilingStarted:
    """Profile the chosen tables (owners and editors) as a background job. 409
    `profiling_unavailable` for a Source System with no live Connection (a Schema Import
    has none): profiling needs one."""
    job = profiling.start_profiling(
        user,
        workspace_id,
        system_id,
        body.table_ids,
        row_cap=body.row_cap,
        timeout_seconds=body.timeout_seconds,
    )
    return ProfilingStarted(job_id=job.id, status=job.status)  # type: ignore[arg-type]


@router.get("/profiling/overview", operation_id="getProfilingOverview")
def get_profiling_overview(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    overview: OverviewServiceDep,
) -> ProfilingOverview:
    """Each table and view of the latest Snapshot with its profiling status and findings
    (any member). A Protected Column gets only the null findings."""
    return ProfilingOverview.model_validate(overview.overview(user, workspace_id, system_id))


@router.get("/profiling/growth", operation_id="getProfilingGrowth")
def get_profiling_growth(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    overview: OverviewServiceDep,
) -> ProfilingGrowth:
    """How each table's row estimate grows across the Snapshots (any member)."""
    return ProfilingGrowth.model_validate(overview.growth(user, workspace_id, system_id))


@router.get("/tables/{table_id}/profile", operation_id="getTableProfile")
def get_table_profile(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    table_id: uuid.UUID,
    user: CurrentUser,
    profiling: ProfilingServiceDep,
) -> TableProfile:
    """A table's profile and its columns' (any member). Min/max and top-N values are
    never shown for a Protected Column."""
    return TableProfile.model_validate(
        profiling.table_profile(user, workspace_id, system_id, table_id)
    )


@router.get("/tables/{table_id}/columns/{column_id}/profile", operation_id="getColumnProfile")
def get_column_profile(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    table_id: uuid.UUID,
    column_id: uuid.UUID,
    user: CurrentUser,
    profiling: ProfilingServiceDep,
) -> ColumnProfileView:
    """One column's profile (any member)."""
    return ColumnProfileView.model_validate(
        profiling.column_profile(user, workspace_id, system_id, table_id, column_id)
    )


@router.put("/tables/{table_id}/profiling-settings", operation_id="setTableTopN")
def set_table_top_n(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    table_id: uuid.UUID,
    body: TopNRequest,
    user: CurrentUser,
    profiling: ProfilingServiceDep,
) -> TableProfile:
    """Switch top-N value capture on or off for a table (owners only; audited). Off by
    default; switching it off deletes the stored values."""
    return TableProfile.model_validate(
        profiling.set_top_n(user, workspace_id, system_id, table_id, body.enabled)
    )
