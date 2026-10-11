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
from dawam.modules.notifications import NotificationService
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
