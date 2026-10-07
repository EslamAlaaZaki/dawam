"""Comments on a Workspace's objects, in resolvable threads, with @mentions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import AuthService, User
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .tables import BODY_MAX_LENGTH, MAX_MENTIONS, OBJECT_TYPES, CommentRecord

NOTIFICATION_REF = "comment"
"""The ``ref_type`` of a mention notification; its ``ref_id`` is the comment."""


@dataclass(frozen=True)
class Person:
    user_id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class Comment:
    id: uuid.UUID
    workspace_id: uuid.UUID
    object_type: str
    object_id: uuid.UUID
    parent_id: uuid.UUID | None
    author: Person | None
    """``None`` when the author no longer exists."""
    body: str
    mentions: list[uuid.UUID]
    resolved_at: datetime | None
    resolved_by: Person | None
    created_at: datetime


@dataclass(frozen=True)
class CommentThread:
    """A root comment with its replies, oldest first."""

    comment: Comment
    replies: list[Comment]


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_comment", message)


class CommentService:
    """Comments of a Workspace. Every method authorizes through the workspaces module's
    policy first. ``object_id`` is opaque here: threads are keyed by Workspace, type and
    id, so a comment can never reach another Workspace's data."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        auth: AuthService,
        notifications: NotificationService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._auth = auth
        self._notifications = notifications
        self._clock = clock

    def add(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        object_type: str,
        object_id: uuid.UUID,
        body: str,
        parent_id: uuid.UUID | None = None,
        mentions: list[uuid.UUID] | None = None,
    ) -> Comment:
        """Comment on an object, or reply to a thread root of the same object (any member,
        viewers included). Each mentioned member except the author gets a ``mention``
        notification, kept only if the comment is. 422 ``invalid_comment`` /
        ``invalid_mention`` (a mentioned user who is not a member)."""
        self._workspaces.authorize(user, Action.COMMENT, workspace_id)
        if object_type not in OBJECT_TYPES:
            raise _invalid(f"Comments go on {', '.join(OBJECT_TYPES)}, not {object_type!r}.")
        body = body.strip()
        if not body or len(body) > BODY_MAX_LENGTH:
            raise _invalid(f"A comment is 1 to {BODY_MAX_LENGTH} characters.")
        mentioned = list(dict.fromkeys(mentions or []))
        if len(mentioned) > MAX_MENTIONS:
            raise _invalid(f"A comment mentions at most {MAX_MENTIONS} people.")
        members = self._workspaces.member_ids_among(workspace_id, mentioned)
        if missing := [m for m in mentioned if m not in members]:
            raise ApiError(
                422,
                "invalid_mention",
                "Only members of the Workspace can be mentioned.",
                details={"user_ids": [str(m) for m in missing]},
            )
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            if parent_id is not None:
                parent = db.get(CommentRecord, parent_id)
                if parent is None or parent.workspace_id != workspace_id:
                    raise ApiError(404, "not_found", "That comment does not exist.")
                if parent.parent_id is not None or (parent.object_type, parent.object_id) != (
                    object_type,
                    object_id,
                ):
                    raise _invalid("Reply to the first comment of a thread on the same object.")
            record = CommentRecord(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                object_type=object_type,
                object_id=object_id,
                parent_id=parent_id,
                author_id=user.id,
                body=body,
                mentions=[str(m) for m in mentioned],
                created_at=now,
            )
            db.add(record)
            db.flush()
            recipients = [m for m in mentioned if m != user.id]
            if recipients:
                self._notifications.notify(
                    recipients,
                    kind="mention",
                    message=f"{user.display_name} mentioned you in a comment on a "
                    f"{object_type.replace('_', ' ')}: {body}",
                    workspace_id=workspace_id,
                    ref_type=NOTIFICATION_REF,
                    ref_id=record.id,
                    db=db,
                )
            return self._views(db, [record])[0]

    def threads(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        object_type: str,
        object_id: uuid.UUID,
    ) -> list[CommentThread]:
        """The object's threads, oldest first, each with its replies (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            roots = list(
                db.scalars(
                    sa.select(CommentRecord)
                    .where(
                        CommentRecord.workspace_id == workspace_id,
                        CommentRecord.object_type == object_type,
                        CommentRecord.object_id == object_id,
                        CommentRecord.parent_id.is_(None),
                    )
                    .order_by(CommentRecord.created_at, CommentRecord.id)
                )
            )
            replies = (
                list(
                    db.scalars(
                        sa.select(CommentRecord)
                        .where(CommentRecord.parent_id.in_([r.id for r in roots]))
                        .order_by(CommentRecord.created_at, CommentRecord.id)
                    )
                )
                if roots
                else []
            )
            views = {c.id: c for c in self._views(db, [*roots, *replies])}
        return [
            CommentThread(
                comment=views[root.id],
                replies=[views[r.id] for r in replies if r.parent_id == root.id],
            )
            for root in roots
        ]

    def resolve(self, user: User, workspace_id: uuid.UUID, comment_id: uuid.UUID) -> Comment:
        """Mark a thread resolved (any member; idempotent). 404 if the comment is not in
        the Workspace; 422 if it is a reply."""
        return self._set_resolved(user, workspace_id, comment_id, resolved=True)

    def reopen(self, user: User, workspace_id: uuid.UUID, comment_id: uuid.UUID) -> Comment:
        """Mark a resolved thread open again (any member; idempotent)."""
        return self._set_resolved(user, workspace_id, comment_id, resolved=False)

    def _set_resolved(
        self, user: User, workspace_id: uuid.UUID, comment_id: uuid.UUID, *, resolved: bool
    ) -> Comment:
        self._workspaces.authorize(user, Action.COMMENT, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = db.get(CommentRecord, comment_id)
            if record is None or record.workspace_id != workspace_id:
                raise ApiError(404, "not_found", "That comment does not exist.")
            if record.parent_id is not None:
                raise _invalid("Only a thread's first comment is resolved.")
            if resolved and record.resolved_at is None:
                record.resolved_at, record.resolved_by = self._clock(), user.id
            elif not resolved:
                record.resolved_at = record.resolved_by = None
            db.flush()
            return self._views(db, [record])[0]

    def _views(self, db: Session, records: list[CommentRecord]) -> list[Comment]:
        ids = {r.author_id for r in records} | {r.resolved_by for r in records}
        users = self._auth.users_by_id(i for i in ids if i is not None)

        def person(user_id: uuid.UUID | None) -> Person | None:
            found = users.get(user_id) if user_id is not None else None
            return Person(found.id, found.display_name) if found else None

        return [
            Comment(
                id=r.id,
                workspace_id=r.workspace_id,
                object_type=r.object_type,
                object_id=r.object_id,
                parent_id=r.parent_id,
                author=person(r.author_id),
                body=r.body,
                mentions=[uuid.UUID(m) for m in r.mentions],
                resolved_at=r.resolved_at,
                resolved_by=person(r.resolved_by),
                created_at=r.created_at,
            )
            for r in records
        ]
