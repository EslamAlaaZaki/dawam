"""``/api/v1/workspaces/{workspace_id}/kpis``: a Workspace's KPI catalog.

Handlers only translate HTTP to ``KpiService`` calls; the service authorizes every call
through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .service import Kpi as KpiView
from .service import KpiService, KpiTarget
from .tables import (
    AGGREGATION_MAX_LENGTH,
    MAX_TARGETS,
    NAME_MAX_LENGTH,
    OWNER_MAX_LENGTH,
    REFRESH_MAX_LENGTH,
    SQL_MAX_LENGTH,
    TARGET_TEXT_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    UNIT_MAX_LENGTH,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/kpis", tags=["kpis"])


def kpi_service(request: Request) -> KpiService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return KpiService(
        state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(state.engine, workspaces=workspaces, clock=clock),
        clock=clock,
    )


KpiServiceDep = Annotated[KpiService, Depends(kpi_service)]

KpiStatus = Literal["draft", "in_review", "approved"]
KpiOrigin = Literal["user", "ai", "rule"]


def _text(max_length: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=max_length)]


Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]
# Looser than the table's limits: the service answers `invalid_kpi`, naming the field.
LongText = _text(TEXT_MAX_LENGTH * 2)
UnitText = _text(UNIT_MAX_LENGTH * 2)
AggregationText = _text(AGGREGATION_MAX_LENGTH * 2)
OwnerText = _text(OWNER_MAX_LENGTH * 2)
RefreshText = _text(REFRESH_MAX_LENGTH * 2)
FormulaSql = Annotated[str, Field(max_length=SQL_MAX_LENGTH * 2)]


class Target(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: Annotated[str, Field(min_length=1, max_length=TARGET_TEXT_MAX_LENGTH * 2)] = Field(
        description="What the target is for, e.g. `FY2027`."
    )
    value: Annotated[str, Field(min_length=1, max_length=TARGET_TEXT_MAX_LENGTH * 2)] = Field(
        description="The target value, e.g. `95%`."
    )


class Kpi(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_system_id: uuid.UUID | None = Field(
        description="The Source System the KPI is documented under; null: the Data Warehouse."
    )
    name: str
    definition: str = Field(description="The business definition.")
    formula_text: str = Field(description="The formula in words.")
    formula_sql: str | None = Field(
        description="The formula as SQL against the DW Schema; null until it is written."
    )
    unit: str
    aggregation: str
    owner: str
    refresh_frequency: str
    targets: list[Target]
    origin: KpiOrigin
    status: KpiStatus
    version: int = Field(description="Send it back when editing; a stale one gets 409.")
    created_at: datetime
    updated_at: datetime


class KpiPage(BaseModel):
    items: list[Kpi]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


class CreateKpiRequest(BaseModel):
    name: Name
    source_system_id: uuid.UUID | None = Field(
        default=None,
        description="Document the KPI under this Source System; null: under the Data "
        "Warehouse (no setup needed).",
    )
    definition: LongText = ""
    formula_text: LongText = ""
    formula_sql: FormulaSql | None = None
    unit: UnitText = ""
    aggregation: AggregationText = ""
    owner: OwnerText = ""
    refresh_frequency: RefreshText = ""
    targets: list[Target] = Field(default=[], max_length=MAX_TARGETS * 2)


class UpdateKpiRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    name: Name | None = None
    definition: LongText | None = None
    formula_text: LongText | None = None
    formula_sql: FormulaSql | None = Field(
        default=None, description="Send null to clear it; leave it out to keep it."
    )
    unit: UnitText | None = None
    aggregation: AggregationText | None = None
    owner: OwnerText | None = None
    refresh_frequency: RefreshText | None = None
    targets: list[Target] | None = Field(default=None, max_length=MAX_TARGETS * 2)
    status: KpiStatus | None = None


def _out(kpi: KpiView) -> Kpi:
    return Kpi.model_validate(kpi)


def _targets(targets: list[Target]) -> list[KpiTarget]:
    return [KpiTarget(label=t.label, value=t.value) for t in targets]


@router.get("", operation_id="listKpis")
def list_kpis(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    kpis: KpiServiceDep,
    source_system_id: Annotated[
        uuid.UUID | None, Query(description="Only this Source System's KPIs.")
    ] = None,
    data_warehouse: Annotated[
        bool, Query(description="Only the Data Warehouse's KPIs (those under no Source System).")
    ] = False,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> KpiPage:
    """The Workspace's KPIs (any member), ordered by name. Without a filter, all of them.
    404 for a Source System that is not in the Workspace; 422 `invalid_filter` if both
    filters are given."""
    page = kpis.list(
        user,
        workspace_id,
        source_system_id=source_system_id,
        data_warehouse=data_warehouse,
        limit=limit,
        cursor=cursor,
    )
    return KpiPage(items=[_out(k) for k in page.items], next_cursor=page.next_cursor)


@router.post("", operation_id="createKpi", status_code=201)
def create_kpi(
    workspace_id: uuid.UUID, body: CreateKpiRequest, user: CurrentUser, kpis: KpiServiceDep
) -> Kpi:
    """Document a KPI as a draft (owners and editors), under a Source System or, with
    `source_system_id` null, under the Data Warehouse (which need not be set up). 404 for
    a Source System that is not in the Workspace; 422 `invalid_kpi`."""
    return _out(
        kpis.create(
            user,
            workspace_id,
            name=body.name,
            source_system_id=body.source_system_id,
            definition=body.definition,
            formula_text=body.formula_text,
            formula_sql=body.formula_sql,
            unit=body.unit,
            aggregation=body.aggregation,
            owner=body.owner,
            refresh_frequency=body.refresh_frequency,
            targets=_targets(body.targets),
        )
    )


@router.get("/{kpi_id}", operation_id="getKpi")
def get_kpi(
    workspace_id: uuid.UUID, kpi_id: uuid.UUID, user: CurrentUser, kpis: KpiServiceDep
) -> Kpi:
    """Open a KPI (any member)."""
    return _out(kpis.get(user, workspace_id, kpi_id))


@router.patch("/{kpi_id}", operation_id="updateKpi")
def update_kpi(
    workspace_id: uuid.UUID,
    kpi_id: uuid.UUID,
    body: UpdateKpiRequest,
    user: CurrentUser,
    kpis: KpiServiceDep,
) -> Kpi:
    """Edit a KPI or move it between `draft`, `in_review` and `approved` (owners and
    editors); fields left out stay as they are. 409 `version_conflict` if `version` is
    stale; 422 `invalid_kpi`."""
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "targets" in changes and body.targets is not None:
        changes["targets"] = _targets(body.targets)
    # Null means "leave as is" for every field but the formula SQL, which it clears.
    changes = {k: v for k, v in changes.items() if v is not None or k == "formula_sql"}
    return _out(kpis.update(user, workspace_id, kpi_id, version=body.version, changes=changes))


@router.delete("/{kpi_id}", operation_id="deleteKpi", status_code=204, response_class=Response)
def delete_kpi(
    workspace_id: uuid.UUID, kpi_id: uuid.UUID, user: CurrentUser, kpis: KpiServiceDep
) -> Response:
    """Delete a KPI (owners and editors)."""
    kpis.delete(user, workspace_id, kpi_id)
    return Response(status_code=204)
