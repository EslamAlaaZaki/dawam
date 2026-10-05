"""``/api/v1/notifications``: the signed-in user's unread notifications."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser

from .service import NotificationKind, NotificationService

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


class UnreadNotifications(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[Notification] = Field(description="The newest unread ones, newest first.")
    unread_count: int = Field(description="All your unread notifications.")


@router.get("", operation_id="listNotifications")
def list_notifications(
    user: CurrentUser, notifications: NotificationServiceDep
) -> UnreadNotifications:
    """Your unread notifications, newest first."""
    return UnreadNotifications.model_validate(notifications.unread(user))


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
