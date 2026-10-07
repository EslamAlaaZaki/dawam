"""``.../systems/{system_id}/relationships``: inferred relationships (spec stories 58, 59).

Handlers only translate HTTP to ``RelationshipService`` calls; the service authorizes
every call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .internal.inference import DEFAULT_THRESHOLD
from .relationship_service import DEFAULT_SAMPLE_SIZE, MAX_SAMPLE_SIZE, RelationshipService

router = APIRouter(tags=["sources"])


def relationship_service(request: Request) -> RelationshipService:
    state = request.app.state
    clock = state.services.clock
    return RelationshipService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=state.services.jobs,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
    )


RelationshipServiceDep = Annotated[RelationshipService, Depends(relationship_service)]

RelationshipStatus = Literal["suggested", "accepted", "rejected"]


class ColumnRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str


class Relationship(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_column: ColumnRef = Field(description="The referencing column.")
    to_column: ColumnRef = Field(description="The referenced (unique) column.")
    origin: Literal["inferred", "routine"] = Field(
        description="`routine` when a view or routine joins the two columns, else `inferred`."
    )
    confidence: float = Field(description="0 to 1, from the signals in `evidence`.")
    evidence: dict[str, Any] = Field(
        description="Each signal that fired with its score and detail: `name`, `type`, "
        "`unique`, `join` (the views and routines) and `overlap` (a ratio, with a live "
        "Connection and profiling). Names and ratios only, never a data value."
    )
    status: RelationshipStatus
    version: int
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


class RelationshipPage(BaseModel):
    items: list[Relationship] = Field(description="Most confident first.")


class InferenceRequest(BaseModel):
    threshold: float = Field(
        default=DEFAULT_THRESHOLD,
        description="Only candidates at or above this confidence (above 0, at most 1) are kept.",
    )
    sample_size: int = Field(
        default=DEFAULT_SAMPLE_SIZE,
        description=f"Rows sampled per table for value overlap (1 to {MAX_SAMPLE_SIZE}); "
        "used only with a live Connection and profiled columns.",
    )


class InferenceStarted(BaseModel):
    job_id: uuid.UUID = Field(
        description="The `infer_relationships` job: follow it at `GET /jobs/{job_id}`."
    )
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]


@router.post("/relationship-inference", operation_id="startRelationshipInference", status_code=202)
def start_relationship_inference(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
    body: InferenceRequest | None = None,
) -> InferenceStarted:
    """Infer undeclared relationships from the latest Snapshot, as a background job (owners
    and editors). Name, type, uniqueness and JOIN conditions in views and routines always
    count; value overlap only with a live Connection and profiled columns. 409
    `no_snapshot` before the first Snapshot; 422 for a bad threshold or sample size."""
    request = body or InferenceRequest()
    job = relationships.start_inference(
        user,
        workspace_id,
        system_id,
        threshold=request.threshold,
        sample_size=request.sample_size,
    )
    return InferenceStarted(job_id=job.id, status=job.status)  # type: ignore[arg-type]


@router.get("/relationships", operation_id="listRelationships")
def list_relationships(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
    status: Annotated[
        RelationshipStatus | None, Query(description="Only relationships in this state.")
    ] = None,
    min_confidence: Annotated[
        float, Query(ge=0, le=1, description="Hide candidates below this confidence.")
    ] = DEFAULT_THRESHOLD,
) -> RelationshipPage:
    """The Source System's inferred relationships with their evidence (any member). Only
    candidates at or above `min_confidence` (default 0.6) are shown."""
    items = relationships.list_relationships(
        user, workspace_id, system_id, status=status, min_confidence=min_confidence
    )
    return RelationshipPage(items=[Relationship.model_validate(i) for i in items])


@router.post("/relationships/{relationship_id}/accept", operation_id="acceptRelationship")
def accept_relationship(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    relationship_id: uuid.UUID,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
) -> Relationship:
    """Accept a relationship (owners and editors): the Source Schema treats it as real.
    Audited."""
    return Relationship.model_validate(
        relationships.accept(user, workspace_id, system_id, relationship_id)
    )


@router.post("/relationships/{relationship_id}/reject", operation_id="rejectRelationship")
def reject_relationship(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    relationship_id: uuid.UUID,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
) -> Relationship:
    """Reject a relationship (owners and editors); later runs do not propose it again.
    Audited."""
    return Relationship.model_validate(
        relationships.reject(user, workspace_id, system_id, relationship_id)
    )
