"""``GET /api/v1/admin/security-events``: the security-event log, for admins only (spec
story 23)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, LoggedSecurityEvent, SecurityEventRecorder, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

router = APIRouter(tags=["admin"])


def security_event_reviewer(user: CurrentUser) -> User:
    """The signed-in user if the central policy lets them review security events
    (admins); anyone else gets ``403 forbidden``."""
    if not can(user, Action.VIEW_SECURITY_EVENTS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")
    return user


SecurityEventReviewer = Annotated[User, Depends(security_event_reviewer)]


def security_events(request: Request) -> SecurityEventRecorder:
    state = request.app.state
    return SecurityEventRecorder(state.engine, clock=state.services.clock)


SecurityEventsDep = Annotated[SecurityEventRecorder, Depends(security_events)]


class SecurityEventOut(BaseModel):
    id: uuid.UUID
    event_type: str = Field(description="What happened, e.g. `login_failed`, `user_deactivated`.")
    actor_id: uuid.UUID | None = Field(description="Who did it, when known.")
    actor_email: str | None = Field(description="The actor's email, if they are a user.")
    target_type: str | None
    target_id: uuid.UUID | None
    metadata: dict[str, Any]
    ip: str | None
    created_at: datetime

    @classmethod
    def of(cls, logged: LoggedSecurityEvent) -> SecurityEventOut:
        event = logged.event
        return cls(
            id=event.id,
            event_type=event.event_type,
            actor_id=event.actor_id,
            actor_email=logged.actor_email,
            target_type=event.target_type,
            target_id=event.target_id,
            metadata=event.metadata,
            ip=event.ip,
            created_at=event.created_at,
        )


class SecurityEventPageOut(BaseModel):
    items: list[SecurityEventOut]
    next_cursor: str | None = Field(description="The `cursor` of the next page; null on the last.")


@router.get("/admin/security-events", operation_id="listSecurityEvents")
def list_security_events(
    _admin: SecurityEventReviewer,
    events: SecurityEventsDep,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: PageCursor = None,
    event_type: Annotated[
        str | None, Query(max_length=64, description="Only events of this type.")
    ] = None,
    actor: Annotated[
        str | None,
        Query(max_length=320, description="Only events done by the user with this email."),
    ] = None,
    since: Annotated[
        datetime | None, Query(description="Only events at or after this time.")
    ] = None,
    until: Annotated[datetime | None, Query(description="Only events before this time.")] = None,
) -> SecurityEventPageOut:
    """Security events, newest first, optionally filtered (admins only)."""
    page = events.search(
        limit=limit,
        cursor=cursor,
        event_type=event_type,
        actor_email=actor,
        since=since,
        until=until,
    )
    return SecurityEventPageOut(
        items=[SecurityEventOut.of(logged) for logged in page.items], next_cursor=page.next_cursor
    )
