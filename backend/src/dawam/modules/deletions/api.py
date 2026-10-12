"""``/api/v1/workspaces/{workspace_id}/deleted``: the "deleted" list and permanent deletes
(spec §7 "Soft deletes").

Handlers only translate HTTP to ``DeletionService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import service as svc
from .service import DeletionService

router = APIRouter(prefix="/workspaces/{workspace_id}/deleted", tags=["deletions"])


def deletion_service(request: Request) -> DeletionService:
    state = request.app.state
    clock = state.services.clock
    return DeletionService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        storage=state.storage,
        clock=clock,
    )


DeletionServiceDep = Annotated[DeletionService, Depends(deletion_service)]


class DeletedSourceSystem(BaseModel):
    id: uuid.UUID
    name: str
    code: str = Field(description="Not reusable by another Source System until it is purged.")
    deleted_at: datetime


class DeletedDwTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    layer: str
    name: str
    kind: str
    column_count: int
    deleted_at: datetime


class DeletedObjects(BaseModel):
    systems: list[DeletedSourceSystem]
    tables: list[DeletedDwTable]


class StagingTableRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    has_mapping: bool = Field(description="Its staging mapping goes with it.")


class BrokenBranch(BaseModel):
    """A mapping branch that reads what goes: it stays, and becomes broken."""

    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    table_name: str
    layer: str
    branch_id: uuid.UUID
    branch_name: str


class FileRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    path: str
    is_document: bool


class KpiRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class SourceSystemDeletion(BaseModel):
    """What purging a deleted Source System does, for the owner to confirm."""

    system: DeletedSourceSystem
    db_schema_count: int
    table_count: int
    column_count: int
    routine_count: int
    snapshot_count: int
    staging_tables: list[StagingTableRef]
    broken_branches: list[BrokenBranch]
    files: list[FileRef]
    source_link_count: int
    rehomed_kpis: list[KpiRef] = Field(
        description="Linked to DW columns: moved to the Data Warehouse."
    )
    deleted_kpis: list[KpiRef] = Field(description="With no DW links: deleted with it.")


class DwTableDeletion(BaseModel):
    """What purging a deleted DW table does, for the owner to confirm."""

    table: DeletedDwTable
    column_names: list[str]
    has_mapping: bool
    broken_branches: list[BrokenBranch]
    unlinked_kpis: list[KpiRef] = Field(description="KPIs that lose their links to its columns.")


def _system(system) -> DeletedSourceSystem:
    return DeletedSourceSystem(
        id=system.id, name=system.name, code=system.code, deleted_at=system.updated_at
    )


@router.get("", operation_id="listDeletedObjects")
def list_deleted(
    workspace_id: uuid.UUID, user: CurrentUser, deletions: DeletionServiceDep
) -> DeletedObjects:
    """The soft-deleted Source Systems and DW tables (any member)."""
    found = deletions.list(user, workspace_id)
    return DeletedObjects(
        systems=[_system(s) for s in found.systems],
        tables=[DeletedDwTable.model_validate(t) for t in found.tables],
    )


@router.get("/systems/{system_id}", operation_id="getSourceSystemDeletion")
def get_system_deletion(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    deletions: DeletionServiceDep,
) -> SourceSystemDeletion:
    """Everything purging a deleted Source System takes with it (owners only)."""
    found: svc.SystemDeletion = deletions.system_deletion(user, workspace_id, system_id)
    return SourceSystemDeletion(
        system=_system(found.system),
        db_schema_count=found.db_schema_count,
        table_count=found.table_count,
        column_count=found.column_count,
        routine_count=found.routine_count,
        snapshot_count=found.snapshot_count,
        staging_tables=[StagingTableRef.model_validate(t) for t in found.staging_tables],
        broken_branches=[BrokenBranch.model_validate(b) for b in found.broken_branches],
        files=[FileRef.model_validate(f) for f in found.files],
        source_link_count=found.source_link_count,
        rehomed_kpis=[KpiRef.model_validate(k) for k in found.rehomed_kpis],
        deleted_kpis=[KpiRef.model_validate(k) for k in found.deleted_kpis],
    )


@router.delete(
    "/systems/{system_id}",
    operation_id="purgeSourceSystem",
    status_code=204,
    response_class=Response,
)
def purge_system(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    deletions: DeletionServiceDep,
) -> Response:
    """Delete a deleted Source System for good (owners only)."""
    deletions.purge_system(user, workspace_id, system_id)
    return Response(status_code=204)


@router.get("/tables/{dw_table_id}", operation_id="getDwTableDeletion")
def get_table_deletion(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    user: CurrentUser,
    deletions: DeletionServiceDep,
) -> DwTableDeletion:
    """Everything purging a deleted DW table takes with it (owners only)."""
    found = deletions.table_deletion(user, workspace_id, dw_table_id)
    return DwTableDeletion(
        table=DeletedDwTable.model_validate(found.table),
        column_names=found.column_names,
        has_mapping=found.has_mapping,
        broken_branches=[BrokenBranch.model_validate(b) for b in found.broken_branches],
        unlinked_kpis=[KpiRef.model_validate(k) for k in found.unlinked_kpis],
    )


@router.delete(
    "/tables/{dw_table_id}",
    operation_id="purgeDwTable",
    status_code=204,
    response_class=Response,
)
def purge_table(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    user: CurrentUser,
    deletions: DeletionServiceDep,
) -> Response:
    """Delete a deleted DW table for good (owners only)."""
    deletions.purge_table(user, workspace_id, dw_table_id)
    return Response(status_code=204)
