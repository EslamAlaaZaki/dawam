"""``GET /api/v1/admin/security-events``: the security-event log, for admins only (spec
story 23)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import LoggedSecurityEvent, SecurityEventRecorder, User
from dawam.modules.workspaces import Action
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, PageCursor, PageLimit

from .internal.access import allowed_to

router = APIRouter(tags=["admin"])


SecurityEventReviewer = Annotated[User, Depends(allowed_to(Action.VIEW_SECURITY_EVENTS))]


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


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


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
    """Security events, newest first, optionally filtered (admins only). A time without
    a time zone is UTC."""
    page = events.search(
        limit=limit,
        cursor=cursor,
        event_type=event_type,
        actor_email=actor,
        since=_utc(since),
        until=_utc(until),
    )
    return SecurityEventPageOut(
        items=[SecurityEventOut.of(logged) for logged in page.items], next_cursor=page.next_cursor
    )
