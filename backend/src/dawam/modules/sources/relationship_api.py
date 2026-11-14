"""``.../systems/{system_id}/relationships``: inferred and manual relationships (spec stories
58 to 60).

Handlers only translate HTTP to ``RelationshipService`` calls; the service authorizes
every call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .internal.inference import DEFAULT_THRESHOLD
from .relationship_service import RelationshipService

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
    origin: Literal["inferred", "routine", "ai", "manual"] = Field(
        description="`routine` when a view or routine joins the two columns, `inferred` when "
        "the rules found it by name, `ai` when only the AI found it, `manual` when an editor "
        "added it."
    )
    confidence: float | None = Field(
        description="0 to 1: from the counts on the source when counted, else from the guess; "
        "`null` for a `manual` relationship."
    )
    evidence: dict[str, Any] = Field(
        description="`found_by` (`rules`, `ai` or `both`); `signals` with each rule signal's "
        "score and detail (`name`, `type`, `unique`, `join`: the views and routines); "
        "`rule_score`; `ai_guess` (the AI's own `confidence` and `reason`); `counts` (`status` "
        "`counted` with the target's rows, values and distinct values, the child's values and "
        "distinct values, the matched and orphan rows and the match rate; or `timed_out`, "
        "`failed` or `no_connection` with a `note`); and `threshold`. `ai` is the Assistant's "
        "evidence for a decision. Names and counts only, never a data value."
    )
    status: RelationshipStatus
    version: int
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


class RelationshipPage(BaseModel):
    items: list[Relationship] = Field(description="Most confident first.")


class ManualRelationshipRequest(BaseModel):
    from_column_id: uuid.UUID = Field(description="The referencing column.")
    to_column_id: uuid.UUID = Field(description="The referenced column.")


class InferenceRequest(BaseModel):
    threshold: float = Field(
        default=DEFAULT_THRESHOLD,
        description="Only candidates at or above this confidence (above 0, at most 1) are kept.",
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
    """Find undeclared relationships from the latest Snapshot, as a background job (owners
    and editors): the rules (names, and JOIN conditions in views and routines) and the AI
    (metadata only, as the Workspace's AI privacy allows) guess pairs side by side, and with
    a live Connection each pair is confirmed by count queries on the source (counts only,
    45 s each). 409 `no_snapshot` before the first Snapshot; 422 for a bad threshold."""
    request = body or InferenceRequest()
    job = relationships.start_inference(
        user,
        workspace_id,
        system_id,
        threshold=request.threshold,
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


@router.post("/relationships", operation_id="createRelationship", status_code=201)
def create_relationship(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: ManualRelationshipRequest,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
) -> Relationship:
    """Add a relationship by hand (owners and editors): origin `manual`, `accepted`, no
    confidence. A pair already suggested or rejected becomes this manual row. 422
    `same_column`; 409 `declared_foreign_key` (the source declares it) or `already_accepted`.
    Audited."""
    return Relationship.model_validate(
        relationships.create_manual(
            user, workspace_id, system_id, body.from_column_id, body.to_column_id
        )
    )


@router.delete(
    "/relationships/{relationship_id}", operation_id="deleteRelationship", status_code=204
)
def delete_relationship(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    relationship_id: uuid.UUID,
    user: CurrentUser,
    relationships: RelationshipServiceDep,
) -> Response:
    """Delete a relationship (owners and editors). A manual one is removed; an inferred one
    is rejected and never suggested again. 409 `declared_relationship` for a declared one.
    Audited."""
    relationships.delete(user, workspace_id, system_id, relationship_id)
    return Response(status_code=204)
