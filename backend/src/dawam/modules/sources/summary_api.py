"""``.../systems/{system_id}/summary``: the Source System dashboard (spec story 63).

Handlers only translate HTTP to ``SourceSummaryService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .summary_service import SourceSummaryService

router = APIRouter(tags=["sources"])


def summary_service(request: Request) -> SourceSummaryService:
    state = request.app.state
    return SourceSummaryService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=state.services.clock)
    )


SummaryServiceDep = Annotated[SourceSummaryService, Depends(summary_service)]


class SourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    system_id: uuid.UUID
    has_snapshot: bool = Field(description="False until the first Snapshot (extraction or import).")
    table_count: int = Field(description="Tables and views in the latest Snapshot.")
    documented_tables: int = Field(description="Of them, those with a description.")
    documented_pct: float = Field(description="0 to 100; 0 when there are no tables.")
    profiled_tables: int = Field(description="Of them, those with at least one column profile.")
    profiled_pct: float = Field(description="0 to 100; 0 when there are no tables.")
    relationships_found: int = Field(description="Suggested and accepted relationships.")
    relationships_accepted: int
    relationships_to_review: int = Field(description="Suggested ones waiting for a decision.")
    pii_found: int = Field(description="Columns with a suggested or confirmed PII finding.")
    pii_confirmed: int
    pii_to_review: int = Field(description="Columns whose finding is still suggested.")
    status: Literal["not_started", "in_progress", "complete"] = Field(
        description="Source Analysis: `complete` once every table is documented and profiled "
        "and no PII finding or relationship waits for review."
    )


@router.get("/summary", operation_id="getSourceSummary")
def get_source_summary(
    workspace_id: uuid.UUID, system_id: uuid.UUID, user: CurrentUser, summaries: SummaryServiceDep
) -> SourceSummary:
    """The Source System dashboard: counts computed from the Source Schema, profiles,
    relationships and PII findings (any member; never a value)."""
    return SourceSummary.model_validate(summaries.summary(user, workspace_id, system_id))
