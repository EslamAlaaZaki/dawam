"""``/api/v1/workspaces/{id}/data-warehouse/score``: the DW score, its failed checks and trend.

Handlers only translate HTTP to ``ScoreService`` calls; the service authorizes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import score_service as svc
from .score_service import ScoreService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse/score", tags=["score"])


def score_service(request: Request) -> ScoreService:
    state = request.app.state
    clock = state.services.clock
    return ScoreService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


ScoreServiceDep = Annotated[ScoreService, Depends(score_service)]

Grade = Literal["A", "B", "C", "D", "F"]


class LayerScore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer: Literal["staging", "core", "mart"]
    score: float | None = Field(description="0-100; null: the Layer is not scored.")
    grade: Grade | None
    scored: bool
    table_count: int


class TableScore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    layer: Literal["staging", "core", "mart"]
    name: str
    score: float


class FailedCheck(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    check_code: str
    title: str
    category: str
    severity: Literal["error", "warning", "info"]
    layer: Literal["staging", "core", "mart"]
    object_type: Literal["table", "column"]
    object_id: uuid.UUID
    table_id: uuid.UUID
    object_name: str
    message: str
    fix_hint: str
    link: str = Field(description="An app path to the object at fault.")


class Score(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    score: float | None = Field(
        description="The Data Warehouse score (Core and Mart), 0-100; null: nothing to score."
    )
    grade: Grade | None
    capped: bool = Field(description="An unresolved error holds the grade at C.")
    calculated_at: datetime
    layers: list[LayerScore] = Field(
        description="Staging is scored apart and is not part of the Data Warehouse score."
    )
    tables: list[TableScore]
    failed_checks: list[FailedCheck] = Field(description="Errors first.")
    checks_passed: int
    checks_failed: int


class ScoreRun(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    score: float | None
    grade: Grade | None
    layers: list[LayerScore]
    created_at: datetime


class ScoreHistory(BaseModel):
    items: list[ScoreRun] = Field(description="Oldest first.")


@router.get("", operation_id="getScore", response_model=Score)
def get_score(workspace_id: uuid.UUID, user: CurrentUser, scores: ScoreServiceDep) -> svc.Score:
    """The Data Warehouse score and grade (any member), a score per Layer ("not scored"
    for a Layer with no tables) and per table, and the failed checks with severity, a link
    to the object and a fix hint. It is recalculated after every design change. 404 before
    the Data Warehouse is set up."""
    return scores.current(user, workspace_id)


@router.get("/history", operation_id="getScoreHistory", response_model=ScoreHistory)
def get_score_history(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    scores: ScoreServiceDep,
    limit: Annotated[int, Query(ge=1, le=svc.KEPT_RUNS)] = svc.HISTORY_LIMIT,
) -> ScoreHistory:
    """The score trend (any member): one summary per recalculation, oldest first, the
    latest `limit` of them."""
    return ScoreHistory(
        items=[ScoreRun.model_validate(r) for r in scores.history(user, workspace_id, limit=limit)]
    )


class StarDimension(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    name: str
    column_id: uuid.UUID = Field(description="The fact's foreign key column.")
    column_name: str
    conformed: bool = Field(description="Shared by several facts, with one meaning.")
    link: str = Field(description="An app path to the foreign key.")


class StarMeasure(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    column_id: uuid.UUID
    name: str
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = Field(
        description="null: not declared yet."
    )
    link: str


class StarCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    layer: Literal["core", "mart"]
    name: str
    fact_type: str | None
    grain: str | None = Field(description="null: no grain statement yet.")
    score: float = Field(description="The fact's own score, 0-100.")
    dimensions: list[StarDimension] = Field(description="Dimensions the fact links to.")
    measures: list[StarMeasure]
    date_dimension: StarDimension | None = Field(description="null: no link to a date dimension.")
    failed_checks: list[FailedCheck] = Field(description="Errors first; each links to its object.")
    link: str


class StarCards(BaseModel):
    items: list[StarCard] = Field(description="Worst score first.")


@router.get("/stars", operation_id="getStarHealthCards", response_model=StarCards)
def get_star_health_cards(
    workspace_id: uuid.UUID, user: CurrentUser, scores: ScoreServiceDep
) -> StarCards:
    """One health card per fact table of Core and Mart (any member): grain, linked
    dimensions with the conformed ones marked, measures with their additivity, the date
    dimension, and the failed checks with links to the objects at fault. Built from the
    latest check results. 404 before the Data Warehouse is set up."""
    return StarCards(items=[StarCard.model_validate(c) for c in scores.stars(user, workspace_id)])
