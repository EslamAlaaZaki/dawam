"""``/api/v1/notifications``: the signed-in user's notifications, read and unread."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.platform.pagination import PageCursor

from .service import NotificationKind, NotificationService

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

router = APIRouter(prefix="/notifications", tags=["notifications"])


def notification_service(request: Request) -> NotificationService:
    state = request.app.state
    return NotificationService(state.engine, clock=state.services.clock)


NotificationServiceDep = Annotated[NotificationService, Depends(notification_service)]


class Notification(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: NotificationKind
    message: str
    workspace_id: uuid.UUID | None
    ref_type: str | None
    ref_id: uuid.UUID | None
    created_at: datetime
    read_at: datetime | None = Field(description="When you read it; null while it is unread.")


class NotificationPage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[Notification] = Field(description="One page of yours, read or not, newest first.")
    unread_count: int = Field(description="All your unread notifications, on every page.")
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


@router.get("", operation_id="listNotifications")
def list_notifications(
    user: CurrentUser,
    notifications: NotificationServiceDep,
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many to return at most.")
    ] = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
) -> NotificationPage:
    """Your notifications, read and unread, newest first, a page at a time."""
    return NotificationPage.model_validate(notifications.page(user, limit=limit, cursor=cursor))


@router.post(
    "/read-all", operation_id="readAllNotifications", status_code=204, response_class=Response
)
def read_all_notifications(user: CurrentUser, notifications: NotificationServiceDep) -> Response:
    """Mark all your notifications read."""
    notifications.mark_all_read(user)
    return Response(status_code=204)


@router.post(
    "/{notification_id}/read",
    operation_id="readNotification",
    status_code=204,
    response_class=Response,
)
def read_notification(
    notification_id: uuid.UUID, user: CurrentUser, notifications: NotificationServiceDep
) -> Response:
    """Mark one of your notifications read. Always 204: an id that is not yours changes
    nothing."""
    notifications.mark_read(user, notification_id)
    return Response(status_code=204)
