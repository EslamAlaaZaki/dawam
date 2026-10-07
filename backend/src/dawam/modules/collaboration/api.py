"""``/api/v1/workspaces/{workspace_id}/comments``: comment threads on a Workspace's objects.

Handlers only translate HTTP to ``CommentService`` calls; the service authorizes every call
through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import AuthService, CurrentUser
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService

from .service import CommentService, CommentThread
from .tables import BODY_MAX_LENGTH

router = APIRouter(prefix="/workspaces/{workspace_id}/comments", tags=["comments"])

ObjectType = Literal["source_table", "source_column", "dw_table", "dw_column", "kpi", "mapping"]


def comment_service(request: Request) -> CommentService:
    state = request.app.state
    clock = state.services.clock
    return CommentService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        auth=AuthService(state.engine, state.settings, clock=clock),
        notifications=NotificationService(state.engine, clock=clock),
        clock=clock,
    )


CommentServiceDep = Annotated[CommentService, Depends(comment_service)]


class Person(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    display_name: str


class Comment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    object_type: ObjectType
    object_id: uuid.UUID
    parent_id: uuid.UUID | None = Field(description="Null for the first comment of a thread.")
    author: Person | None = Field(description="Null once the author's account is gone.")
    body: str
    mentions: list[uuid.UUID] = Field(description="The members the comment @mentions.")
    resolved_at: datetime | None = Field(description="Set on a resolved thread's first comment.")
    resolved_by: Person | None
    created_at: datetime


class CommentThreadOut(BaseModel):
    id: uuid.UUID
    comment: Comment
    replies: list[Comment] = Field(description="Oldest first.")


class CommentThreads(BaseModel):
    items: list[CommentThreadOut] = Field(description="The object's threads, oldest first.")


class AddCommentRequest(BaseModel):
    object_type: ObjectType
    object_id: uuid.UUID = Field(description="The commented object's id.")
    body: Annotated[str, Field(min_length=1, max_length=BODY_MAX_LENGTH * 2)]
    parent_id: uuid.UUID | None = Field(
        default=None, description="Reply to this thread's first comment."
    )
    mentions: list[uuid.UUID] = Field(
        default_factory=list, description="Workspace members to notify."
    )


def _thread(thread: CommentThread) -> CommentThreadOut:
    return CommentThreadOut(
        id=thread.comment.id,
        comment=Comment.model_validate(thread.comment),
        replies=[Comment.model_validate(r) for r in thread.replies],
    )


@router.get("", operation_id="listComments")
def list_comments(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    comments: CommentServiceDep,
    object_type: Annotated[ObjectType, Query()],
    object_id: Annotated[uuid.UUID, Query()],
) -> CommentThreads:
    """The comment threads on one object, with their replies (any member)."""
    found = comments.threads(user, workspace_id, object_type=object_type, object_id=object_id)
    return CommentThreads(items=[_thread(t) for t in found])


@router.post("", operation_id="addComment", status_code=201)
def add_comment(
    workspace_id: uuid.UUID, body: AddCommentRequest, user: CurrentUser, comments: CommentServiceDep
) -> Comment:
    """Comment on an object, or reply to a thread (any member, viewers included).
    Mentioned members get an in-app `mention` notification. 422 `invalid_comment`,
    `invalid_mention`."""
    return Comment.model_validate(
        comments.add(
            user,
            workspace_id,
            object_type=body.object_type,
            object_id=body.object_id,
            body=body.body,
            parent_id=body.parent_id,
            mentions=body.mentions,
        )
    )


@router.post("/{comment_id}/resolve", operation_id="resolveComment")
def resolve_comment(
    workspace_id: uuid.UUID, comment_id: uuid.UUID, user: CurrentUser, comments: CommentServiceDep
) -> Comment:
    """Resolve a thread (any member). 422 for a reply: resolve its thread's first comment."""
    return Comment.model_validate(comments.resolve(user, workspace_id, comment_id))


@router.post("/{comment_id}/reopen", operation_id="reopenComment")
def reopen_comment(
    workspace_id: uuid.UUID, comment_id: uuid.UUID, user: CurrentUser, comments: CommentServiceDep
) -> Comment:
    """Reopen a resolved thread (any member)."""
    return Comment.model_validate(comments.reopen(user, workspace_id, comment_id))
