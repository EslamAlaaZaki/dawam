"""What admins do across Workspaces (spec §4.3, stories 21, 22): see every Workspace's
metadata and rescue one whose owners are all deactivated.

Admins who are not members never see a Workspace's content: the list below holds only
names, owners, member counts, state and dates, and reassigning ownership is the one
way an admin gets into a Workspace, which every member is told about.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import AuthService, SecurityEventRecorder, User
from dawam.modules.notifications import NotificationService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from ..service import WorkspaceStatus, as_status, authorized
from ..tables import MemberRecord, WorkspaceRecord
from .policy import INSTALLATION, Action, can


@dataclass(frozen=True)
class WorkspaceOwner:
    user_id: uuid.UUID
    email: str
    display_name: str
    is_active: bool


@dataclass(frozen=True)
class AdminWorkspace:
    """A Workspace as an admin sees it: metadata only, never content."""

    id: uuid.UUID
    name: str
    status: WorkspaceStatus
    owners: list[WorkspaceOwner]
    member_count: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None

    @property
    def has_active_owner(self) -> bool:
        return any(owner.is_active for owner in self.owners)


@dataclass(frozen=True)
class AdminWorkspacePage:
    items: list[AdminWorkspace]
    next_cursor: str | None
    """Pass it back as ``cursor`` for the next page; ``None`` on the last page."""


class WorkspaceAdministration:
    """Admin-only views and rescues; every method authorizes through the policy first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        clock: Clock,
        auth: AuthService,
        events: SecurityEventRecorder,
        notifications: NotificationService,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._auth = auth
        self._events = events
        self._notifications = notifications

    def list_all(
        self, user: User, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> AdminWorkspacePage:
        """Every Workspace by name, with its owners, member count and dates. Raises
        ``ApiError`` 403 ``forbidden`` (not an admin), 422 ``invalid_cursor``."""
        if not can(user, Action.LIST_ALL_WORKSPACES, INSTALLATION):
            raise ApiError(403, "forbidden", "Only admins can do this.")
        sort_key = sa.func.lower(WorkspaceRecord.name)
        query = (
            sa.select(WorkspaceRecord, sort_key)
            .order_by(sort_key, WorkspaceRecord.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            after_name, after_id = decode_cursor(cursor, 2)
            try:
                after_uuid = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.") from None
            query = query.where(
                sa.tuple_(sort_key, WorkspaceRecord.id)
                > sa.tuple_(sa.literal(after_name, sa.String), sa.literal(after_uuid, sa.Uuid))
            )
        with Session(self._engine) as db:
            rows = db.execute(query).all()
            items = self._views(db, [record for record, _ in rows[:limit]])
        next_cursor = None
        if len(rows) > limit:
            # The database's own sort key, so the next page starts exactly after it.
            last, last_key = rows[limit - 1]
            next_cursor = encode_cursor(last_key, str(last.id))
        return AdminWorkspacePage(items=items, next_cursor=next_cursor)

    def reassign_owner(
        self,
        user: User,
        workspace_id: uuid.UUID,
        new_owner_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> AdminWorkspace:
        """Make the active user ``new_owner_id`` an owner of a Workspace none of whose
        owners is active any more (the member they are, or a new one), as the admin
        ``user``. The server checks this under the Workspace lock and refuses otherwise.
        Writes a ``workspace_ownership_reassigned`` security event and notifies every
        member (active ones; the new owner included).

        Raises ``ApiError`` 404 ``not_found`` (Workspace) / ``user_not_found``, 409
        ``has_active_owner``, 422 ``user_inactive``."""
        target = self._auth.get_user(new_owner_id)
        with Session(self._engine) as db, db.begin():
            record, _ = authorized(db, user, Action.REASSIGN_OWNERSHIP, workspace_id, lock=True)
            owner_ids = db.scalars(
                sa.select(MemberRecord.user_id).where(
                    MemberRecord.workspace_id == workspace_id, MemberRecord.role == "owner"
                )
            ).all()
            if any(owner.is_active for owner in self._auth.users_by_id(owner_ids).values()):
                raise ApiError(
                    409,
                    "has_active_owner",
                    "This Workspace still has an active owner, so its ownership cannot be "
                    "reassigned.",
                )
            if target is None:
                raise ApiError(404, "user_not_found", "No user has that id.")
            if not target.is_active:
                raise ApiError(422, "user_inactive", "A deactivated user cannot become an owner.")
            member = db.get(MemberRecord, (workspace_id, target.id))
            previous_role = member.role if member is not None else None
            if member is None:
                db.add(
                    MemberRecord(
                        workspace_id=workspace_id,
                        user_id=target.id,
                        role="owner",
                        added_by=user.id,
                        added_at=self._clock(),
                    )
                )
            else:
                member.role = "owner"
            db.flush()
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="workspace.ownership_reassigned",
                object_type="user",
                object_id=target.id,
                object_label=target.display_name,
                details={"role": "owner"},
                at=self._clock(),
            )
            self._events.record(
                "workspace_ownership_reassigned",
                actor_id=user.id,
                target_type="workspace",
                target_id=workspace_id,
                metadata={
                    "name": record.name,
                    "new_owner_id": str(target.id),
                    "previous_role": previous_role,
                },
                ip=ip,
                db=db,
            )
            member_ids = db.scalars(
                sa.select(MemberRecord.user_id).where(MemberRecord.workspace_id == workspace_id)
            ).all()
            active = [m.id for m in self._auth.users_by_id(member_ids).values() if m.is_active]
            self._notifications.notify(
                active,
                kind="ownership",
                message=(
                    f"An admin made {target.display_name} the owner of the Workspace "
                    f"“{record.name}”, which had no active owner."
                ),
                workspace_id=workspace_id,
                ref_type="workspace",
                ref_id=workspace_id,
                db=db,
            )
        return self._one(workspace_id)

    def _one(self, workspace_id: uuid.UUID) -> AdminWorkspace:
        with Session(self._engine) as db:
            record = db.get(WorkspaceRecord, workspace_id)
            if record is None:  # pragma: no cover - deleted since: only a race
                raise ApiError(404, "not_found", "Workspace not found.")
            return self._views(db, [record])[0]

    def _views(self, db: Session, records: list[WorkspaceRecord]) -> list[AdminWorkspace]:
        ids = [record.id for record in records]
        owners_of: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in ids}
        counts: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
        for workspace_id, user_id, role in db.execute(
            sa.select(MemberRecord.workspace_id, MemberRecord.user_id, MemberRecord.role).where(
                MemberRecord.workspace_id.in_(ids)
            )
        ):
            counts[workspace_id] += 1
            if role == "owner":
                owners_of[workspace_id].append(user_id)
        users = self._auth.users_by_id(uid for owners in owners_of.values() for uid in owners)
        return [
            AdminWorkspace(
                id=record.id,
                name=record.name,
                status=as_status(record.status),
                owners=sorted(
                    (_owner(users[uid]) for uid in owners_of[record.id]),
                    key=lambda o: (o.display_name.casefold(), o.email),
                ),
                member_count=counts[record.id],
                created_at=record.created_at,
                updated_at=record.updated_at,
                archived_at=record.archived_at,
            )
            for record in records
        ]


def _owner(user: User) -> WorkspaceOwner:
    return WorkspaceOwner(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
    )
