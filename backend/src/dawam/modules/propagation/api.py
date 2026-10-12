"""``POST /api/v1/workspaces/{workspace_id}/fix-impacts``: recompute propagation (story 113a).

The handler only translates HTTP to ``PropagationService.fix_impacts``, which authorizes
through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.changesets import change_set_service
from dawam.modules.kpis import plan_kpi_transpile
from dawam.modules.notifications import NotificationService
from dawam.modules.warehouse import DwChangeService, TargetPlatform
from dawam.modules.workspaces import WorkspaceService

from .service import PropagationService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["propagation"])


def propagation_service(request: Request) -> PropagationService:
    state = request.app.state
    clock = state.services.clock
    return PropagationService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        change_sets=change_set_service(request),
        notifications=NotificationService(state.engine, clock=clock),
    )


PropagationServiceDep = Annotated[PropagationService, Depends(propagation_service)]


def dw_change_service(request: Request) -> DwChangeService:
    state = request.app.state
    clock = state.services.clock
    return DwChangeService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        change_sets=change_set_service(request),
        clock=clock,
        extra_plans=[plan_kpi_transpile],
    )


DwChangeServiceDep = Annotated[DwChangeService, Depends(dw_change_service)]


class ImpactOut(BaseModel):
    layer: str = Field(description="`staging`, `core`, `mart` or `kpi`.")
    object_type: str = Field(description="`dw_table`, `dw_column` or `kpi`.")
    object_id: uuid.UUID
    label: str = Field(description="`table.column`, the table name or the KPI name.")


class FixImpactsResult(BaseModel):
    change_set_id: uuid.UUID | None = Field(
        description="The new `propagation` Change Set; null when nothing broken can be fixed "
        "by propagation (what is left stays a validation error to fix by hand)."
    )
    impacts: list[ImpactOut] = Field(description="Every impacted Asset, grouped by Layer.")


@router.post("/fix-impacts", operation_id="fixImpacts")
def fix_impacts(
    workspace_id: uuid.UUID, user: CurrentUser, propagation: PropagationServiceDep
) -> FixImpactsResult:
    """Recompute the propagation Change Set from whatever is broken now: the open validation
    errors propagation can fix (a mapping reading a renamed, removed or `source_removed`
    input). Owners and editors; 409 `workspace_archived` while the Workspace is archived.
    Expired items are never revived: a new Change Set is proposed, and a newer "Fix
    impacts" supersedes a pending one."""
    result = propagation.fix_impacts(user, workspace_id)
    return FixImpactsResult(
        change_set_id=result.change_set.change_set.id if result.change_set else None,
        impacts=[ImpactOut.model_validate(i, from_attributes=True) for i in result.impacts],
    )


class ProposedChangeSet(BaseModel):
    change_set_id: uuid.UUID = Field(
        description="The pending Change Set, to review at `/change-sets/{id}`."
    )
    items: int = Field(description="How many changes it proposes.")
    manual: list[str] = Field(
        description='Stored SQL sqlglot could not translate, flagged "manual" (platform changes).'
    )


class SystemCodeChangeRequest(BaseModel):
    code: str = Field(max_length=200, description="The new System Code.")


class PlatformChangeRequest(BaseModel):
    target_platform: TargetPlatform


@router.post("/systems/{system_id}/code-change", operation_id="proposeSystemCodeChange")
def propose_system_code_change(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: SystemCodeChangeRequest,
    user: CurrentUser,
    changes: DwChangeServiceDep,
) -> ProposedChangeSet:
    """Propose changing a Source System's System Code (owners only; 403 for editors). One
    `system_code_change` Change Set renames every Staging Table named after the old code and
    updates the `source_system` audit column; accepting it applies the change, and the Core and
    Mart SQL reading a renamed table follows through the propagation Change Set the rename
    triggers. A pending one for the same system is superseded. 404 `not_found`; 409
    `system_code_unchanged`; 422 `invalid_change_set` for a code that is not identifier-safe
    or is taken."""
    detail = changes.propose_system_code_change(user, workspace_id, system_id, body.code)
    return ProposedChangeSet(change_set_id=detail.change_set.id, items=len(detail.items), manual=[])


@router.post("/data-warehouse/platform-change", operation_id="proposePlatformChange")
def propose_platform_change(
    workspace_id: uuid.UUID,
    body: PlatformChangeRequest,
    user: CurrentUser,
    changes: DwChangeServiceDep,
) -> ProposedChangeSet:
    """Propose moving the Data Warehouse to another target platform (owners only; 403 for
    editors). One `platform_change` Change Set carries the platform, the Staging column types
    translated again for it and every stored SQL text (Core and Mart mappings and branches, KPI
    formulas) transpiled to its dialect; text sqlglot cannot translate stays as it is and is
    flagged "manual" (`manual`, and a `manual_review` item). Accepting the platform is
    structural: it returns an approved Core and Mart to draft. 404 `not_set_up`; 409
    `platform_unchanged`."""
    proposed = changes.propose_platform_change(user, workspace_id, body.target_platform)
    return ProposedChangeSet(
        change_set_id=proposed.change_set.change_set.id,
        items=len(proposed.change_set.items),
        manual=[f"{n.label}: {n.reason}" for n in proposed.manual],
    )
