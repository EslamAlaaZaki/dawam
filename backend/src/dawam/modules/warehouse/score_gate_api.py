"""``/api/v1/workspaces/{id}/data-warehouse/score``: disabled checks and the approval gates.

Handlers only translate HTTP to ``ScoreGateService`` calls; the service authorizes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import score_gate as svc
from .score_api import DisabledCheck, score_service
from .score_gate import REASON_MAX_LENGTH, ScoreGateService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse/score", tags=["score"])


def score_gate_service(request: Request) -> ScoreGateService:
    state = request.app.state
    clock = state.services.clock
    return ScoreGateService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        clock=clock,
        scores=score_service(request),
    )


ScoreGateServiceDep = Annotated[ScoreGateService, Depends(score_gate_service)]

GateLayer = Literal["core", "mart"]


class DisabledChecks(BaseModel):
    items: list[DisabledCheck] = Field(description="By check code.")


class DisableCheckRequest(BaseModel):
    reason: str = Field(max_length=REASON_MAX_LENGTH, description="Why the check is off.")


class ScoreGate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer: GateLayer
    min_score: float = Field(ge=0, le=100)
    configured: bool = Field(description="False: no minimum is set (errors still block approval).")
    updated_by: uuid.UUID | None
    updated_at: datetime | None


class ScoreGates(BaseModel):
    items: list[ScoreGate] = Field(description="Core, then Mart. Staging has no gate.")


class SetGateRequest(BaseModel):
    min_score: float = Field(ge=0, le=100, description="Approval needs the Layer at or above it.")


def _disabled(items: list[svc.DisabledCheck]) -> DisabledChecks:
    return DisabledChecks(items=[DisabledCheck.model_validate(i) for i in items])


@router.get("/disabled-checks", operation_id="listDisabledChecks")
def list_disabled_checks(
    workspace_id: uuid.UUID, user: CurrentUser, gates: ScoreGateServiceDep
) -> DisabledChecks:
    """The checks switched off for the Data Warehouse, with their reasons (any member).
    They are left out of the score. 404 `not_set_up` before set up."""
    return _disabled(gates.disabled(user, workspace_id))


@router.put("/disabled-checks/{check_code}", operation_id="disableCheck")
def disable_check(
    workspace_id: uuid.UUID,
    check_code: str,
    body: DisableCheckRequest,
    user: CurrentUser,
    gates: ScoreGateServiceDep,
) -> DisabledChecks:
    """Switch a score check off with a reason, or change the reason (owners only). The score
    is recalculated without it. 404 `unknown_check`; 422 `reason_required`."""
    return _disabled(gates.disable(user, workspace_id, check_code, body.reason))


@router.delete("/disabled-checks/{check_code}", operation_id="enableCheck")
def enable_check(
    workspace_id: uuid.UUID, check_code: str, user: CurrentUser, gates: ScoreGateServiceDep
) -> DisabledChecks:
    """Switch a disabled check back on (owners only); the score is recalculated. 404
    `not_disabled`."""
    return _disabled(gates.enable(user, workspace_id, check_code))


@router.get("/gates", operation_id="listScoreGates")
def list_score_gates(
    workspace_id: uuid.UUID, user: CurrentUser, gates: ScoreGateServiceDep
) -> ScoreGates:
    """The approval gate of the Core and Mart Layers (any member)."""
    return ScoreGates(items=[ScoreGate.model_validate(g) for g in gates.gates(user, workspace_id)])


@router.put("/gates/{layer}", operation_id="setScoreGate")
def set_score_gate(
    workspace_id: uuid.UUID,
    layer: GateLayer,
    body: SetGateRequest,
    user: CurrentUser,
    gates: ScoreGateServiceDep,
) -> ScoreGate:
    """Set the minimum score of the Core or Mart Layer and whether errors block approval
    (owners only). Approving the Layer then fails with 409 `score_gate_failed` while it has
    errors or scores below the minimum. Staging has no gate."""
    return ScoreGate.model_validate(gates.set_gate(user, workspace_id, layer, body.min_score))
