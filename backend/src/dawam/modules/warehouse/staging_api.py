"""``/api/v1/workspaces/{id}/data-warehouse/staging``: generate the Staging Layer (spec §6.7).

Handlers only translate HTTP to ``StagingService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.changesets import ChangeSetDetail, ChangeSetService, ObjectHandlers
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService

from .staging_service import StagingService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/staging", tags=["data-warehouse-staging"]
)


def staging_service(request: Request) -> StagingService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    notifications = NotificationService(state.engine, clock=clock)
    return StagingService(
        state.engine,
        workspaces=workspaces,
        clock=clock,
        notifications=notifications,
        change_sets=ChangeSetService(
            state.engine,
            workspaces=workspaces,
            handlers=getattr(state, "change_set_handlers", None) or ObjectHandlers(),
            notifications=notifications,
            clock=clock,
            conversations=getattr(state, "readable_conversations", None),
        ),
    )


StagingServiceDep = Annotated[StagingService, Depends(staging_service)]


class StagingFlag(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    table_name: str
    column_name: str | None = Field(description="Null for a flag on the table itself.")
    code: str = Field(
        description="`placeholder`, `truncated`, `collision`, `lossy_type` or `fallback_type`."
    )
    message: str


class StagingResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tables_created: int
    columns_created: int = Field(description="Including the audit columns.")
    tables_existing: int = Field(
        description="Source tables that already had a Staging Table; left as they are."
    )
    flags: list[StagingFlag] = Field(description="What to review, for the tables just created.")


@router.post("/generate", operation_id="generateStaging")
def generate_staging(
    workspace_id: uuid.UUID, user: CurrentUser, staging: StagingServiceDep
) -> StagingResult:
    """Generate the Staging Layer from the Source Schema (owners and editors; software,
    no AI): one Staging Table per source base table (and per view opted in), named
    `stg_<system code>_<database schema>_<table>`, with translated column types, the audit
    columns, and `direct` mappings and lineage from the source. Names and types that needed
    a placeholder, a hash suffix or a lossy translation are flagged for review. Running it
    again adds only what is new. 404 `not_set_up` before the Data Warehouse is set up."""
    return StagingResult.model_validate(staging.generate(user, workspace_id))


class ProposedChangeSet(BaseModel):
    change_set_id: uuid.UUID | None = Field(
        description="The pending Change Set, to review at `/change-sets/{id}`; null when there "
        "is nothing to change."
    )
    items: int = Field(description="How many changes it proposes.")
    conflicts: int = Field(description="Items that would overwrite a field a user overrode.")


def _proposed(detail: ChangeSetDetail | None) -> ProposedChangeSet:
    if detail is None:
        return ProposedChangeSet(change_set_id=None, items=0, conflicts=0)
    return ProposedChangeSet(
        change_set_id=detail.change_set.id,
        items=len(detail.items),
        conflicts=sum(1 for i in detail.items if i.is_conflict),
    )


class SyncRequest(BaseModel):
    source_system_id: uuid.UUID


@router.post("/sync", operation_id="syncStaging")
def sync_staging(
    workspace_id: uuid.UUID, body: SyncRequest, user: CurrentUser, staging: StagingServiceDep
) -> ProposedChangeSet:
    """Propose a **sync** Change Set for one Source System from its latest Snapshot (owners
    and editors). It also runs by itself after every new Snapshot. Staging only: new tables
    and columns are created, changed columns updated, tables and columns dropped in the source
    kept and flagged `source_removed`. Tables an editor deleted are not proposed again, and a
    field a user overrode is a conflict item. Nothing changes until the Change Set is
    accepted; it replaces a pending sync of the same Source System. 404 `not_found`."""
    return _proposed(staging.sync(user, workspace_id, body.source_system_id))


@router.post("/drop-removed", operation_id="dropRemovedStaging")
def drop_removed_staging(
    workspace_id: uuid.UUID, user: CurrentUser, staging: StagingServiceDep
) -> ProposedChangeSet:
    """Propose a **drop removed** Change Set (owners and editors may ask; only an owner can
    accept its items): delete the `source_removed` staging tables and columns that no mapping
    reads. A Staging Table deleted this way keeps a Tombstone. 404 `not_set_up` before the Data
    Warehouse is set up."""
    return _proposed(staging.drop_removed(user, workspace_id))
