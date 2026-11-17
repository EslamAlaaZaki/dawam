"""``.../systems/{system_id}/catalog-suggestions``: AI data catalog suggestions (spec §6.19,
#626): start the ``catalog_suggest`` job and read its latest run, list the suggestions, and
accept (as written or edited) or reject them.

Handlers only translate HTTP to ``CatalogSuggestionService`` calls; the service authorizes
every call through the workspaces module's policy (owners and editors; viewers get 403).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.jobs import Job
from dawam.modules.workspaces import WorkspaceService

from .catalog_suggestion_service import CatalogSuggestionService, Decided
from .tables import DESCRIPTION_MAX_LENGTH

router = APIRouter(prefix="/catalog-suggestions", tags=["sources"])


def catalog_suggestion_service(request: Request) -> CatalogSuggestionService:
    state = request.app.state
    clock = state.services.clock
    return CatalogSuggestionService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=state.services.jobs,
        clock=clock,
    )


CatalogSuggestionServiceDep = Annotated[
    CatalogSuggestionService, Depends(catalog_suggestion_service)
]

SuggestionStatus = Literal["pending", "accepted", "rejected", "expired"]
CatalogField = Literal["description", "classification", "scd_hint"]


class CatalogSuggestionJob(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued", "running", "paused", "succeeded", "failed", "cancelled"]
    progress: int = Field(description="Percent done, 0 to 100.")
    error: str | None = Field(description="Why the job failed; null otherwise.")
    made: int | None = Field(
        description="Suggestions the run made; null until the job has succeeded."
    )
    skipped: int | None = Field(
        description="Fields the run skipped because they already had a value or the AI "
        "repeated a value people rejected; null until the job has succeeded."
    )
    created_at: datetime
    finished_at: datetime | None


class LatestCatalogSuggestionJob(BaseModel):
    job: CatalogSuggestionJob | None = Field(description="The newest run; null if none ran yet.")


class CatalogSuggestion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column_id: uuid.UUID | None = Field(description="Null for a suggestion on the table.")
    column: str | None
    field: CatalogField = Field(description="A column's suggestions are `description` only.")
    value: str = Field(description="What the AI suggested.")
    edited_value: str | None = Field(
        description="What a person accepted instead (edit-then-accept); null otherwise."
    )
    current_value: str | None = Field(description="The object's value for the field now.")
    status: SuggestionStatus
    job_id: uuid.UUID | None = Field(description="The run that made it.")
    created_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


class CatalogSuggestionCounts(BaseModel):
    pending: int
    accepted: int
    rejected: int
    expired: int


class CatalogSuggestionPage(BaseModel):
    items: list[CatalogSuggestion]
    counts: CatalogSuggestionCounts = Field(
        description="The Source System's suggestions in each status, with no filter."
    )


class SuggestionIds(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)


class AcceptAll(BaseModel):
    table_id: uuid.UUID | None = Field(
        default=None, description="Accept only this table's pending suggestions."
    )


class AcceptEdited(BaseModel):
    id: uuid.UUID
    value: str = Field(
        min_length=1,
        max_length=DESCRIPTION_MAX_LENGTH,
        description="The value to save instead of the AI's: a person's value, with no AI mark.",
    )


class DecidedSuggestions(BaseModel):
    decided: list[CatalogSuggestion]
    skipped: list[uuid.UUID] = Field(
        description="Suggestions asked for that were no longer pending (decided or expired)."
    )


def _job(job: Job) -> CatalogSuggestionJob:
    result = job.result or {}

    def count(key: str) -> int | None:
        value = result.get(key)
        return value if isinstance(value, int) and job.status == "succeeded" else None

    return CatalogSuggestionJob(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]  # the jobs table's CHECK values
        progress=job.progress,
        error=job.error,
        made=count("made"),
        skipped=count("skipped"),
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def _decided(decided: Decided) -> DecidedSuggestions:
    return DecidedSuggestions(
        decided=[CatalogSuggestion.model_validate(s) for s in decided.decided],
        skipped=decided.skipped,
    )


@router.post("", operation_id="startCatalogSuggestions", status_code=202)
def start_catalog_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> CatalogSuggestionJob:
    """Suggest the empty descriptions, classifications and SCD hints of this Source System's
    tables and columns with the AI, as a background job (owners and editors who may ask the
    assistant). It fills gaps only and replaces the pending suggestions of the run before.
    409 `no_snapshot` before the first Snapshot; 409 `catalog_suggestions_running` while a
    run is going for this system."""
    return _job(catalog.start(user, workspace_id, system_id))


@router.get("", operation_id="getCatalogSuggestionJob")
def get_catalog_suggestion_job(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> LatestCatalogSuggestionJob:
    """The newest catalog suggestion run of this Source System, with how many suggestions it
    made and skipped once it has succeeded (owners and editors)."""
    job = catalog.latest(user, workspace_id, system_id)
    return LatestCatalogSuggestionJob(job=_job(job) if job is not None else None)


@router.get("/items", operation_id="listCatalogSuggestions")
def list_catalog_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
    status: Annotated[SuggestionStatus, Query()] = "pending",
    table_id: Annotated[uuid.UUID | None, Query()] = None,
) -> CatalogSuggestionPage:
    """The Source System's suggestions in one status (default `pending`), of one table when
    `table_id` is given, ordered by table, table fields first (owners and editors)."""
    page = catalog.list_suggestions(user, workspace_id, system_id, status=status, table_id=table_id)
    return CatalogSuggestionPage(
        items=[CatalogSuggestion.model_validate(s) for s in page.items],
        counts=CatalogSuggestionCounts(**page.counts),
    )


@router.post("/accept", operation_id="acceptCatalogSuggestions")
def accept_catalog_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: SuggestionIds,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> DecidedSuggestions:
    """Accept one or many pending suggestions as the AI wrote them (owners and editors): each
    value is saved on its table or column, audited as the AI's, and shows an AI tag until
    someone edits it. Ones no longer pending are skipped; 404 for an id not of this system."""
    return _decided(catalog.accept(user, workspace_id, system_id, body.ids))


@router.post("/accept-all", operation_id="acceptAllCatalogSuggestions")
def accept_all_catalog_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: AcceptAll,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> DecidedSuggestions:
    """Accept every pending suggestion of this Source System, or of one table (owners and
    editors)."""
    return _decided(catalog.accept_all(user, workspace_id, system_id, table_id=body.table_id))


@router.post("/accept-edited", operation_id="acceptEditedCatalogSuggestion")
def accept_edited_catalog_suggestion(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: AcceptEdited,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> CatalogSuggestion:
    """Accept one pending suggestion with your own value instead (owners and editors): it
    counts as a person's value, with no AI tag. 409 `suggestion_not_pending`; 422
    `invalid_enhancement` for a value the field does not take."""
    return CatalogSuggestion.model_validate(
        catalog.accept_edited(user, workspace_id, system_id, body.id, body.value)
    )


@router.post("/reject", operation_id="rejectCatalogSuggestions")
def reject_catalog_suggestions(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: SuggestionIds,
    user: CurrentUser,
    catalog: CatalogSuggestionServiceDep,
) -> DecidedSuggestions:
    """Reject one or many pending suggestions (owners and editors); a rejected value is never
    suggested again for that object and field. Ones no longer pending are skipped."""
    return _decided(catalog.reject(user, workspace_id, system_id, body.ids))
