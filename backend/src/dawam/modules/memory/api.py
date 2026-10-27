"""``/api/v1/workspaces/{workspace_id}/memory``: the Workspace's approved memory items.

Items are proposed by the assistant and approved as Change Set items, so this router only
reads. The service authorizes every call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .service import MemoryService

router = APIRouter(prefix="/workspaces/{workspace_id}/memory", tags=["memory"])


def memory_service(request: Request) -> MemoryService:
    state = request.app.state
    return MemoryService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=state.services.clock)
    )


MemoryServiceDep = Annotated[MemoryService, Depends(memory_service)]


class MemoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    text: str
    created_at: datetime


class MemoryItemList(BaseModel):
    items: list[MemoryItem]


@router.get("", operation_id="listMemoryItems")
def list_memory_items(
    workspace_id: uuid.UUID, user: CurrentUser, memory: MemoryServiceDep
) -> MemoryItemList:
    """The Workspace's approved memory items, newest first (any member)."""
    return MemoryItemList(
        items=[MemoryItem.model_validate(i) for i in memory.list(user, workspace_id)]
    )
