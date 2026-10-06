"""``.../systems/{system_id}/extractions`` and ``.../snapshots``: metadata extraction into
Snapshots (spec stories 45, 46).

Handlers only translate HTTP to ``SnapshotService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .snapshot_service import SnapshotContent as SnapshotContentRecord
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


ObjectStatus = Annotated[
    Literal["present", "source_removed", "out_of_scope", "deleted"],
    Field(
        description="The Source Object's state now: `present`, `source_removed` (gone from "
        "the source), `out_of_scope` (outside the allowed Database Schemas) or `deleted`. "
        "Set only in the Source Schema: a stored Snapshot never changes."
    ),
]


class SnapshotDbSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str
    status: ObjectStatus | None = None


class SnapshotColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str
    status: ObjectStatus | None = None
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
    status: ObjectStatus | None = None
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
    status: ObjectStatus | None = None
    signature: str = Field(description="The argument list, which tells overloads apart.")
    definition: str | None


class SnapshotContent(SnapshotSummary):
    db_schemas: list[SnapshotDbSchema]
    tables: list[SnapshotTable]
    routines: list[SnapshotRoutine]


class RemovedTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object.")
    db_schema: str
    name: str
    kind: Literal["table", "view"]
    status: Literal["source_removed", "out_of_scope"]


class RemovedColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object.")
    table_id: uuid.UUID
    name: str
    status: Literal["source_removed", "out_of_scope"]
    data_type: str | None = Field(description="As of the latest Snapshot that had it.")


class SourceSchema(SnapshotContent):
    removed_tables: list[RemovedTable] = Field(
        description="Tables and views the latest Snapshot no longer has, still tracked and "
        "flagged `source_removed` or `out_of_scope`."
    )
    removed_columns: list[RemovedColumn] = Field(
        description="Columns of the tables above the Snapshot no longer has, still tracked "
        "and flagged `source_removed` or `out_of_scope`."
    )


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["db_schema", "table", "view", "column", "routine"]
    id: uuid.UUID = Field(description="The Source Object.")
    name: str
    db_schema: str | None = Field(description="Null for a Database Schema itself.")
    table: str | None = Field(description="A column's table.")
    status: ObjectStatus


class SearchResults(BaseModel):
    items: list[SearchHit] = Field(
        description="Exact name matches first, then names starting with the query."
    )


Change = Literal["added", "removed", "changed"]


class FieldChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field: str
    before: str | None
    after: str | None


class ColumnChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str
    change: Change
    fields: list[FieldChange]


class TableChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    db_schema: str
    name: str
    kind: Literal["table", "view"]
    change: Change = Field(
        description="`changed` also when only its columns changed (see `columns`)."
    )
    fields: list[FieldChange]
    columns: list[ColumnChange] = Field(
        description="Added, removed and changed columns; empty for an added or removed table."
    )


class RoutineChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    db_schema: str
    name: str
    kind: Literal["procedure", "function"]
    signature: str
    change: Change
    fields: list[FieldChange]


class DbSchemaChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="The Source Object: the same in every Snapshot.")
    name: str
    change: Change
    fields: list[FieldChange]


class SnapshotDiff(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_snapshot_id: uuid.UUID = Field(description="The base (`against_id`).")
    to_snapshot_id: uuid.UUID
    db_schemas: list[DbSchemaChange]
    tables: list[TableChange]
    routines: list[RoutineChange]


def _content_body(content: SnapshotContentRecord) -> dict:
    return {
        **SnapshotSummary.model_validate(content.snapshot).model_dump(),
        "db_schemas": content.db_schemas,
        "tables": content.tables,
        "routines": content.routines,
    }


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
    return SnapshotContent.model_validate(_content_body(content))


@router.get("/snapshots/{snapshot_id}/diff/{against_id}", operation_id="diffSnapshots")
def diff_snapshots(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    against_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
) -> SnapshotDiff:
    """What changed from the `against_id` Snapshot to this one (any member): added, removed
    and changed Database Schemas, tables, views (with their columns) and routines, matched
    by identity so a rename or type change is a change. Any two Snapshots of the Source
    System can be compared. 404 for a Snapshot of another Source System."""
    diff = snapshots.diff(user, workspace_id, system_id, snapshot_id, against_id)
    return SnapshotDiff.model_validate(diff)


@router.get("/schema", operation_id="getSourceSchema")
def get_source_schema(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
) -> SourceSchema:
    """The Source Schema to browse (any member): the latest Snapshot (Database Schemas,
    tables and views with columns, keys, indexes and view definitions, routines with
    their code), every object with its state, plus the tables and views it no longer has.
    404 before the first Snapshot."""
    schema = snapshots.source_schema(user, workspace_id, system_id)
    return SourceSchema.model_validate(
        {
            **_content_body(schema.content),
            "removed_tables": schema.removed_tables,
            "removed_columns": schema.removed_columns,
        }
    )


@router.get("/schema/search", operation_id="searchSourceSchema")
def search_source_schema(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    snapshots: SnapshotServiceDep,
    q: Annotated[str, Query(max_length=200, description="Part of a name; case-insensitive.")] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchResults:
    """Search the latest Snapshot's Database Schemas, tables, views, columns and routines
    by name (any member). An empty query finds nothing; 404 before the first Snapshot."""
    hits = snapshots.search(user, workspace_id, system_id, q, limit)
    return SearchResults(items=[SearchHit.model_validate(h) for h in hits])
