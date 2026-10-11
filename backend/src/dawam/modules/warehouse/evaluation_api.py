"""``/api/v1/workspaces/{id}/data-warehouse/evaluations``: the stored AI evaluations of a Layer.

Handlers only translate HTTP to ``EvaluationService`` calls; the service authorizes. An
evaluation is made by the assistant's ``evaluate_dw`` tool, not by an endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.changesets import ChangeSetService, change_set_service
from dawam.modules.workspaces import WorkspaceService

from .evaluation_service import EvaluationService
from .model_service import ModelService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/evaluations", tags=["evaluations"]
)


def evaluation_service(request: Request) -> EvaluationService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return EvaluationService(
        state.engine,
        workspaces=workspaces,
        model=ModelService(state.engine, workspaces=workspaces, clock=clock),
        clock=clock,
    )


EvaluationServiceDep = Annotated[EvaluationService, Depends(evaluation_service)]
ChangeSetServiceDep = Annotated[ChangeSetService, Depends(change_set_service)]


class FindingItem(BaseModel):
    object_type: str
    operation: Literal["create", "update", "delete"]
    object_id: uuid.UUID | None
    payload: dict[str, Any]
    label: str


class Finding(BaseModel):
    title: str
    detail: str
    category: Literal[
        "ambiguous_grain",
        "scd2_candidate",
        "missing_conformed_dimension",
        "uncomputable_kpi",
        "other",
    ]
    severity: Literal["high", "medium", "low"]
    table_id: uuid.UUID | None
    suggestion: str
    items: list[FindingItem] = Field(
        description="Concrete changes the finding proposes; empty: advice only."
    )


class AiEvaluation(BaseModel):
    id: uuid.UUID
    layer: Literal["staging", "core", "mart"]
    findings: list[Finding] = Field(
        description="Advisory: they never change the score or the gate."
    )
    created_by: uuid.UUID | None
    created_at: datetime


class AiEvaluationList(BaseModel):
    items: list[AiEvaluation] = Field(description="Newest first.")


class FindingChangeSet(BaseModel):
    change_set_id: uuid.UUID
    title: str
    item_count: int


@router.get("", operation_id="listAiEvaluations", response_model=AiEvaluationList)
def list_evaluations(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    evaluations: EvaluationServiceDep,
    layer: Annotated[Literal["staging", "core", "mart"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> AiEvaluationList:
    """The stored AI evaluations, newest first (any member). 404 before the Data Warehouse
    is set up."""
    return AiEvaluationList(
        items=[
            AiEvaluation.model_validate(e, from_attributes=True)
            for e in evaluations.list(user, workspace_id, layer=layer, limit=limit)
        ]
    )


@router.post(
    "/{evaluation_id}/findings/{index}/change-set",
    operation_id="createChangeSetFromFinding",
    response_model=FindingChangeSet,
    status_code=201,
)
def create_change_set(
    workspace_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    index: int,
    user: CurrentUser,
    evaluations: EvaluationServiceDep,
    change_sets: ChangeSetServiceDep,
) -> FindingChangeSet:
    """Turn a finding's concrete changes into a pending Change Set (owners and editors).
    404 for an unknown evaluation or finding; 422 `no_changes` for advice only."""
    detail = evaluations.propose_change_set(
        user, workspace_id, evaluation_id, index, change_sets=change_sets
    )
    return FindingChangeSet(
        change_set_id=detail.change_set.id,
        title=detail.change_set.title,
        item_count=len(detail.items),
    )
