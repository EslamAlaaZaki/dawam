"""``.../systems/{system_id}/extractions`` and ``.../snapshots``: metadata extraction into
Snapshots (spec stories 45, 46).

Handlers only translate HTTP to ``SnapshotService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .snapshot_service import SnapshotService

router = APIRouter(tags=["sources"])


def snapshot_service(request: Request) -> SnapshotService:
    state = request.app.state
    clock = state.services.clock
    return SnapshotService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        jobs=state.services.jobs,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
    )


SnapshotServiceDep = Annotated[SnapshotService, Depends(snapshot_service)]


class ExtractionStarted(BaseModel):
    job_id: uuid.UUID = Field(
        description="The `extract` job: follow its status, progress and log at "
        "`GET /jobs/{job_id}`."
    )
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]


class SnapshotSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_system_id: uuid.UUID
    origin: Literal["connection", "import"]
    job_id: uuid.UUID | None = Field(description="The job that produced it.")
    taken_at: datetime
    is_latest: bool
    schema_count: int
    table_count: int = Field(description="Tables and views.")
    column_count: int
    routine_count: int


class SnapshotPage(BaseModel):
    items: list[SnapshotSummary] = Field(description="Newest first.")


class SnapshotDbSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str


class SnapshotColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str
    ordinal: int
    data_type: str
    is_nullable: bool
    is_pk: bool
    default: str | None
    comment: str | None


class SnapshotConstraint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    type: Literal["pk", "fk", "unique"]
    columns: list[str]
    ref_table_id: uuid.UUID | None = Field(
        description="A foreign key's referenced table, when it is in this Snapshot."
    )
    ref_db_schema: str | None
    ref_table: str | None
    ref_columns: list[str]


class SnapshotIndex(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    columns: list[str] = Field(description="Column names; an expression key is its SQL.")
    is_unique: bool


class SnapshotTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    db_schema: str
    name: str
    kind: Literal["table", "view"]
    view_definition: str | None
    row_estimate: int | None
    comment: str | None
    columns: list[SnapshotColumn]
    constraints: list[SnapshotConstraint]
    indexes: list[SnapshotIndex]


class SnapshotRoutine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    db_schema: str
    name: str
    kind: Literal["procedure", "function"]
    signature: str = Field(description="The argument list, which tells overloads apart.")
    definition: str | None


class SnapshotContent(SnapshotSummary):
    db_schemas: list[SnapshotDbSchema]
    tables: list[SnapshotTable]
    routines: list[SnapshotRoutine]


@router.post("/extractions", operation_id="startExtraction", status_code=202)
def start_extraction(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
) -> ExtractionStarted:
    """Extract metadata from the Source System's Connection into a new Snapshot, as a
    background job (owners and editors). A source with no differences from the latest
    Snapshot creates none. 409 `connection_missing` without a Connection."""
    job = snapshots.start_extraction(user, workspace_id, system_id)
    return ExtractionStarted(job_id=job.id, status=job.status)  # type: ignore[arg-type]


@router.get("/snapshots", operation_id="listSnapshots")
def list_snapshots(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
) -> SnapshotPage:
    """The Source System's Snapshots, newest first (any member)."""
    items = snapshots.list(user, workspace_id, system_id)
    return SnapshotPage(items=[SnapshotSummary.model_validate(s) for s in items])


@router.get("/snapshots/{snapshot_id}", operation_id="getSnapshot")
def get_snapshot(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
) -> SnapshotContent:
    """Everything one Snapshot captured: Database Schemas, tables and views (with
    columns, constraints, indexes and view definitions) and routines with their code
    (any member)."""
    content = snapshots.get(user, workspace_id, system_id, snapshot_id)
    return SnapshotContent.model_validate(
        {
            **SnapshotSummary.model_validate(content.snapshot).model_dump(),
            "db_schemas": content.db_schemas,
            "tables": content.tables,
            "routines": content.routines,
        }
    )
