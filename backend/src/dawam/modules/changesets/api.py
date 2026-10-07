"""``/api/v1/workspaces/{workspace_id}/change-sets``: review proposed changes.

Handlers only translate HTTP to ``ChangeSetService`` calls; the service authorizes every
call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from . import service as engine
from .handlers import ObjectHandlers
from .service import ChangeSetService

router = APIRouter(prefix="/workspaces/{workspace_id}/change-sets", tags=["change-sets"])


def change_set_service(request: Request) -> ChangeSetService:
    state = request.app.state
    clock = state.services.clock
    return ChangeSetService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        handlers=getattr(state, "change_set_handlers", None) or ObjectHandlers(),
        notifications=NotificationService(state.engine, clock=clock),
        clock=clock,
        conversations=getattr(state, "readable_conversations", None),
    )


ChangeSetServiceDep = Annotated[ChangeSetService, Depends(change_set_service)]

Origin = Literal[
    "ai", "regeneration", "sync", "propagation", "import", "platform_change", "system_code_change"
]
ChangeSetStatus = Literal["pending", "applied", "partially_applied", "rejected", "superseded"]
ItemStatus = Literal["pending", "needs_owner", "accepted", "rejected", "stale", "expired"]


class ChangeSetItem(BaseModel):
    id: uuid.UUID
    position: int
    object_type: str = Field(description="What the item changes, e.g. `source_table`.")
    object_id: uuid.UUID | None
    operation: Literal["create", "update", "delete"]
    label: str = Field(description="What the diff shows for the object, e.g. `core.customers`.")
    base_values: dict[str, Any] | None = Field(
        description="The values of the changed fields when the item was proposed (the diff's "
        "'before'); an item is stale when one of them has changed since."
    )
    payload: dict[str, Any] = Field(description="The new values (the diff's 'after').")
    depends_on: list[uuid.UUID]
    required_role: Literal["editor", "owner"]
    is_conflict: bool
    status: ItemStatus
    status_reason: str | None


class ChangeSet(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    origin: Origin
    scope: dict[str, Any]
    title: str
    conversation_id: uuid.UUID | None
    created_by: uuid.UUID | None
    status: ChangeSetStatus
    created_at: datetime
    applied_by: uuid.UUID | None
    applied_at: datetime | None
    item_counts: dict[str, int] = Field(description="Items by status.")


class ChangeSetDetail(BaseModel):
    change_set: ChangeSet
    items: list[ChangeSetItem]


class ChangeSetPage(BaseModel):
    items: list[ChangeSet]
    next_cursor: str | None


class DecisionRequest(BaseModel):
    item_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=engine.MAX_ITEMS,
        description="The items to decide; leave out for every open item.",
    )


class SkippedItem(BaseModel):
    item_id: uuid.UUID
    reason: Literal["stale", "depends_on_skipped"]
    detail: str


class ApplyResult(BaseModel):
    change_set: ChangeSetDetail
    accepted: list[uuid.UUID]
    skipped: list[SkippedItem] = Field(
        description="Stale items and the items that depend on them, not applied."
    )
    needs_owner: list[uuid.UUID] = Field(
        description="Items an owner still has to accept (owner-only ones and what depends on them)."
    )


def _detail(detail: engine.ChangeSetDetail) -> ChangeSetDetail:
    return ChangeSetDetail.model_validate(detail, from_attributes=True)


@router.get("", operation_id="listChangeSets")
def list_change_sets(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    change_sets: ChangeSetServiceDep,
    status: Annotated[ChangeSetStatus | None, Query(description="Only this status.")] = None,
    conversation_id: Annotated[
        uuid.UUID | None, Query(description="Only those the assistant proposed in this chat.")
    ] = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> ChangeSetPage:
    """The Workspace's Change Sets, newest first (any member)."""
    return ChangeSetPage.model_validate(
        change_sets.list(
            user,
            workspace_id,
            status=status,
            conversation_id=conversation_id,
            limit=limit,
            cursor=cursor,
        ),
        from_attributes=True,
    )


@router.get("/{change_set_id}", operation_id="getChangeSet")
def get_change_set(
    workspace_id: uuid.UUID,
    change_set_id: uuid.UUID,
    user: CurrentUser,
    change_sets: ChangeSetServiceDep,
) -> ChangeSetDetail:
    """A Change Set with every item, as a diff (any member). 404 if not in the Workspace."""
    return _detail(change_sets.get(user, workspace_id, change_set_id))


@router.post("/{change_set_id}/accept", operation_id="acceptChangeSet")
def accept_change_set(
    workspace_id: uuid.UUID,
    change_set_id: uuid.UUID,
    body: DecisionRequest,
    user: CurrentUser,
    change_sets: ChangeSetServiceDep,
) -> ApplyResult:
    """Accept all or some items (owners and editors) and apply them in one transaction.
    What they depend on is accepted too. Stale items and their dependents are skipped and
    reported; owner-only items stay `needs_owner` unless an owner accepts. 409
    `change_set_closed`; 422 `invalid_change_set`."""
    result = change_sets.accept(user, workspace_id, change_set_id, body.item_ids)
    return ApplyResult(
        change_set=_detail(result.change_set),
        accepted=result.accepted,
        skipped=[SkippedItem.model_validate(s, from_attributes=True) for s in result.skipped],
        needs_owner=result.needs_owner,
    )


@router.post("/{change_set_id}/reject", operation_id="rejectChangeSet")
def reject_change_set(
    workspace_id: uuid.UUID,
    change_set_id: uuid.UUID,
    body: DecisionRequest,
    user: CurrentUser,
    change_sets: ChangeSetServiceDep,
) -> ChangeSetDetail:
    """Reject all or some items (owners and editors); items that depend on them are
    rejected too. 409 `change_set_closed`; 422 `invalid_change_set`."""
    return _detail(change_sets.reject(user, workspace_id, change_set_id, body.item_ids))
