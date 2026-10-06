"""``.../systems/{system_id}/rename-candidates`` and ``.../renames``: renamed Source
Objects (spec story 52a).

Handlers only translate HTTP to ``RenameService`` calls; the service authorizes every
call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .rename_service import RenameService

router = APIRouter(tags=["sources"])

ObjectType = Literal["db_schema", "table", "column"]


def rename_service(request: Request) -> RenameService:
    state = request.app.state
    clock = state.services.clock
    return RenameService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


RenameServiceDep = Annotated[RenameService, Depends(rename_service)]


class RenameCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    snapshot_id: uuid.UUID = Field(description="The Snapshot that proposed it.")
    object_type: ObjectType
    old_object_id: uuid.UUID = Field(description="The removed Source Object.")
    old_name: str
    location: str | None = Field(
        description="A table's Database Schema; a column's `schema.table`."
    )
    new_object_id: uuid.UUID = Field(description="The added Source Object.")
    new_name: str
    confidence: float = Field(description="0 to 1: how alike the two objects are.")
    status: Literal["suggested", "confirmed", "rejected"]


class RenameCandidateList(BaseModel):
    items: list[RenameCandidate] = Field(
        description="The open candidates of the latest Snapshot, most confident first."
    )


class MergeRequest(BaseModel):
    object_type: ObjectType
    removed_id: uuid.UUID = Field(description="The Source Object that is gone from the source.")
    added_id: uuid.UUID = Field(description="The Source Object of the same kind that was added.")


class RenamedObject(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    object_type: ObjectType
    id: uuid.UUID = Field(description="The surviving Source Object: the one that was removed.")
    name: str
    previous_name: str


@router.get("/rename-candidates", operation_id="listRenameCandidates")
def list_rename_candidates(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    renames: RenameServiceDep,
) -> RenameCandidateList:
    """Removed columns, tables and Database Schemas that look like added ones (any
    member): the open candidates of the latest Snapshot."""
    return RenameCandidateList(
        items=[
            RenameCandidate.model_validate(c)
            for c in renames.list_candidates(user, workspace_id, system_id)
        ]
    )


@router.post("/rename-candidates/{candidate_id}/confirm", operation_id="confirmRename")
def confirm_rename(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    candidate_id: uuid.UUID,
    user: CurrentUser,
    renames: RenameServiceDep,
) -> RenamedObject:
    """Confirm a rename (owners and editors): the removed object keeps its identity,
    descriptions and mappings under the new name. Audited. 409 `rename_conflict` if it was
    already decided or an object changed."""
    return RenamedObject.model_validate(
        renames.confirm(user, workspace_id, system_id, candidate_id)
    )


@router.post(
    "/rename-candidates/{candidate_id}/reject", operation_id="rejectRename", status_code=204
)
def reject_rename(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    candidate_id: uuid.UUID,
    user: CurrentUser,
    renames: RenameServiceDep,
) -> None:
    """Reject a rename (owners and editors): the removal and the addition stand."""
    renames.reject(user, workspace_id, system_id, candidate_id)


@router.post("/renames", operation_id="mergeRemovedObject")
def merge_removed_object(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    body: MergeRequest,
    user: CurrentUser,
    renames: RenameServiceDep,
) -> RenamedObject:
    """Merge a removed object into an added one of the same kind, for a rename noticed
    late (owners and editors); audited. 409 `rename_conflict` if they are not a removed
    and an added object of the same kind (and, for columns, table)."""
    return RenamedObject.model_validate(
        renames.merge(
            user,
            workspace_id,
            system_id,
            object_type=body.object_type,
            removed_id=body.removed_id,
            added_id=body.added_id,
        )
    )
