"""``/api/v1/workspaces/{id}/data-warehouse/approvals``: the Core and Mart Layer Approval.

Handlers only translate HTTP to ``ApprovalService`` calls; the service authorizes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import approvals as svc
from .approvals import ApprovalService
from .score_api import score_service
from .score_gate import ScoreGateService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/approvals", tags=["data-warehouse"]
)


def approval_service(request: Request) -> ApprovalService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    gates = ScoreGateService(
        state.engine, workspaces=workspaces, clock=clock, scores=score_service(request)
    )
    return ApprovalService(
        state.engine, workspaces=workspaces, clock=clock, gate=gates.verify_approval
    )


ApprovalServiceDep = Annotated[ApprovalService, Depends(approval_service)]

ApprovalLayer = Literal["core", "mart"]
ApprovalStatus = Literal["draft", "in_review", "approved"]


class LayerApproval(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer: ApprovalLayer
    status: ApprovalStatus = Field(
        description="`in_review` only asks for a review; a structural change to the Layer "
        "returns `approved` to `draft`."
    )
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    last_editor_id: uuid.UUID | None = Field(description="Who last changed anything in the Layer.")
    last_edited_at: datetime | None


class LayerApprovalList(BaseModel):
    items: list[LayerApproval] = Field(description="Core, then Mart.")


class MoveLayerRequest(BaseModel):
    status: ApprovalStatus


@router.get("", operation_id="listLayerApprovals")
def list_layer_approvals(
    workspace_id: uuid.UUID, user: CurrentUser, approvals: ApprovalServiceDep
) -> LayerApprovalList:
    """The approval state of the Core and Mart Layers (any member). Staging has none.
    404 `not_set_up` before the Data Warehouse is set up."""
    return LayerApprovalList(
        items=[LayerApproval.model_validate(a) for a in approvals.list(user, workspace_id)]
    )


@router.put("/{layer}", operation_id="moveLayerApproval")
def move_layer_approval(
    workspace_id: uuid.UUID,
    layer: ApprovalLayer,
    body: MoveLayerRequest,
    user: CurrentUser,
    approvals: ApprovalServiceDep,
) -> LayerApproval:
    """Move the Core or Mart Layer to `draft`, `in_review` or `approved` (owners and
    editors). 409 `core_not_approved` when approving Mart while Core is not approved;
    409 `approver_is_last_editor` when the Workspace requires another approver than the
    Layer's last editor; 409 `score_gate_failed` when the owner set a gate on the Layer and
    it has errors or scores below its minimum. Moving Core away from `approved` returns an
    approved Mart to `draft`. Every move is in the audit trail."""
    result: svc.LayerApproval = approvals.move(user, workspace_id, layer, body.status)
    return LayerApproval.model_validate(result)
