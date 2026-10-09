from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import SecurityEventRecorder, User
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.hooks import (
    ModelingProgressProvider,
    SourceAnalysisProvider,
    WorkspaceArchivedHook,
    WorkspaceCreatedHook,
)
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .internal.policy import (
    INSTALLATION,
    WORKSPACE_ACTIONS,
    WORKSPACE_ROLES,
    Action,
    WorkspaceRole,
    WorkspaceScope,
    can,
)
from .tables import (
    DESCRIPTION_MAX_LENGTH,
    DOMAIN_MAX_LENGTH,
    MEMBER_PRIMARY_KEY,
    NAME_MAX_LENGTH,
    MemberRecord,
    WorkspaceRecord,
)

WorkspaceStatus = Literal["active", "archived"]


@dataclass(frozen=True)
class Workspace:
    """A Workspace as one of its members sees it."""

    id: uuid.UUID
    name: str
    description: str
    domain: str
    role: WorkspaceRole
    """The viewing member's role."""
    permissions: frozenset[Action]
    """The Workspace actions the viewing member may perform (from ``can``)."""
    status: WorkspaceStatus
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


StageStatus = Literal["not_started", "in_progress", "complete"]
Layer = Literal["staging", "core", "mart"]
LAYERS: tuple[Layer, ...] = ("staging", "core", "mart")


@dataclass(frozen=True)
class SystemProgress:
    """Source Analysis of one Source System."""

    system_id: uuid.UUID
    name: str
    status: StageStatus


@dataclass(frozen=True)
class LayerProgress:
    """DW Modeling of one Layer."""

    layer: Layer
    status: StageStatus


@dataclass(frozen=True)
class KpiProgress:
    status: StageStatus


@dataclass(frozen=True)
class StageProgress:
    """Where a Workspace's work stands. Later tickets fill in the statuses (and the
    Source Systems) from what they build; today nothing can be started."""

    source_analysis: list[SystemProgress]
    kpis: KpiProgress
    dw_modeling: list[LayerProgress]


@dataclass(frozen=True)
class WorkspacePage:
    items: list[Workspace]
    next_cursor: str | None
    """Pass it back as ``cursor`` for the next page; ``None`` on the last page."""


def _not_found() -> ApiError:
    # Also what non-members get for a Workspace that exists, so they cannot tell.
    return ApiError(404, "not_found", "Workspace not found.")


def _forbidden() -> ApiError:
    return ApiError(403, "forbidden", "Your role in this Workspace does not allow this.")


def _archived() -> ApiError:
    return ApiError(
        409,
        "workspace_archived",
        "This Workspace is archived and read-only. Unarchive it to change it.",
    )


def _not_archived() -> ApiError:
    return ApiError(409, "workspace_not_archived", "This Workspace must be archived first.")


def _denied(user: User, action: Action, scope: WorkspaceScope) -> ApiError:
    """Why ``user`` may not ``action`` in ``scope``. If the Workspace's state alone is in
    the way (they could, were it archived or not), say so; otherwise 403 for members and
    404 for everyone else."""
    if can(user, action, replace(scope, archived=not scope.archived)):
        return _archived() if scope.archived else _not_archived()
    return _forbidden() if scope.role is not None else _not_found()


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Someone else changed this Workspace since you loaded it. Reload and try again.",
        {"current_version": current},
    )


def _clean(value: str, field: str, max_length: int, *, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise ApiError(
            422, "invalid_workspace", f"The {field} must not be empty.", {"field": field}
        )
    if len(cleaned) > max_length:
        raise ApiError(
            422,
            "invalid_workspace",
            f"The {field} must be at most {max_length} characters.",
            {"field": field},
        )
    return cleaned


def as_role(value: str) -> WorkspaceRole:
    """``value`` (a stored role) as a ``WorkspaceRole``; ``ValueError`` if unknown."""
    if value not in WORKSPACE_ROLES:
        raise ValueError(f"unknown Workspace role {value!r}")
    return value  # type: ignore[return-value]  # checked above


def authorized(
    db: Session,
    user: User,
    action: Action,
    workspace_id: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[WorkspaceRecord, WorkspaceScope]:
    """Load the Workspace ``workspace_id`` in ``db`` and check ``user`` may perform
    ``action`` there (404 ``not_found`` / 403 ``forbidden`` as ``WorkspaceService.authorize``).
    ``lock`` takes the Workspace row ``FOR UPDATE``, as every change of members must."""
    query = (
        sa.select(WorkspaceRecord, MemberRecord.role)
        .outerjoin(
            MemberRecord,
            (MemberRecord.workspace_id == WorkspaceRecord.id) & (MemberRecord.user_id == user.id),
        )
        .where(WorkspaceRecord.id == workspace_id)
    )
    if lock:
        query = query.with_for_update(of=WorkspaceRecord)
    row = db.execute(query).first()
    if row is None:
        raise _not_found()
    record, role = row
    scope = WorkspaceScope(
        workspace_id=record.id,
        user_id=user.id,
        role=as_role(role) if role is not None else None,
        archived=record.status == "archived",
    )
    if can(user, action, scope):
        return record, scope
    raise _denied(user, action, scope)


def workspace_view(user: User, record: WorkspaceRecord, role: WorkspaceRole | None) -> Workspace:
    """``record`` as the member ``user``, whose role is ``role``, sees it."""
    if role is None:
        # Only members get a Workspace; an admin-only action never returns one.
        raise ValueError("a Workspace is only shown to its members")
    scope = WorkspaceScope(
        workspace_id=record.id,
        user_id=user.id,
        role=role,
        archived=record.status == "archived",
    )
    return Workspace(
        id=record.id,
        name=record.name,
        description=record.description,
        domain=record.domain,
        role=role,
        permissions=frozenset(a for a in WORKSPACE_ACTIONS if can(user, a, scope)),
        status=as_status(record.status),
        archived_at=record.archived_at,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def as_status(value: str) -> WorkspaceStatus:
    if value not in ("active", "archived"):
        raise ValueError(f"unknown Workspace status {value!r}")
    return value  # type: ignore[return-value]  # checked above


class WorkspaceService:
    """Workspaces and their members. Every method that acts for a user authorizes
    through the policy (``can``) first; callers add no checks of their own."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        clock: Clock,
        events: SecurityEventRecorder | None = None,
        on_archived: WorkspaceArchivedHook | None = None,
        source_analysis: SourceAnalysisProvider | None = None,
        on_created: WorkspaceCreatedHook | None = None,
        modeling_progress: ModelingProgressProvider | None = None,
    ) -> None:
        """``events`` records Workspace deletions; ``delete`` needs it. ``on_archived`` is
        called in the transaction that archives a Workspace (the composition root sets
        it, e.g. to cancel the Workspace's jobs); ``on_created`` in the one that creates
        it (e.g. to give it its AI settings). ``source_analysis`` tells the stage progress
        how far each Source System's analysis is (without it: no systems);
        ``modeling_progress`` how far each Layer of the DW Schema is (without it: not started)."""
        self._engine = engine
        self._clock = clock
        self._events = events
        self._on_archived = on_archived
        self._source_analysis = source_analysis
        self._on_created = on_created
        self._modeling_progress = modeling_progress

    def authorize(self, user: User, action: Action, workspace_id: uuid.UUID) -> WorkspaceScope:
        """Check that ``user`` may perform ``action`` in the Workspace ``workspace_id``
        and return its scope.

        ``workspace_id`` must come from the resource on the server (e.g. the Source
        System's own ``workspace_id``), never from client input alone. Raises
        ``ApiError`` 404 ``not_found`` if the Workspace does not exist or the user may
        not see it (non-members, admins included, never learn it exists), and 403
        ``forbidden`` if a member's role does not allow the action.
        """
        with Session(self._engine) as db:
            _, scope = authorized(db, user, action, workspace_id)
            return scope

    def create(
        self, user: User, *, name: str, description: str = "", domain: str = ""
    ) -> Workspace:
        """Create a Workspace with ``user`` as its owner."""
        if not can(user, Action.CREATE_WORKSPACE, INSTALLATION):
            raise ApiError(403, "forbidden", "You may not create Workspaces.")
        now = self._clock()
        record = WorkspaceRecord(
            id=uuid.uuid4(),
            name=_clean(name, "name", NAME_MAX_LENGTH, required=True),
            description=_clean(description, "description", DESCRIPTION_MAX_LENGTH),
            domain=_clean(domain, "domain", DOMAIN_MAX_LENGTH),
            created_by=user.id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            db.add(
                MemberRecord(
                    workspace_id=record.id,
                    user_id=user.id,
                    role="owner",
                    added_by=user.id,
                    added_at=now,
                )
            )
            record_activity(
                db,
                workspace_id=record.id,
                actor_id=user.id,
                verb="workspace.created",
                object_type="workspace",
                object_id=record.id,
                object_label=record.name,
                at=now,
            )
            if self._on_created is not None:
                self._on_created(db, record.id, now)
            return workspace_view(user, record, "owner")

    def list_for(
        self, user: User, *, limit: int = DEFAULT_PAGE_SIZE, cursor: str | None = None
    ) -> WorkspacePage:
        """The Workspaces ``user`` is a member of, with their role, ordered by name."""
        sort_key = sa.func.lower(WorkspaceRecord.name)
        query = (
            sa.select(WorkspaceRecord, MemberRecord.role, sort_key)
            .join(MemberRecord, MemberRecord.workspace_id == WorkspaceRecord.id)
            .where(MemberRecord.user_id == user.id)
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
        items = [workspace_view(user, record, as_role(role)) for record, role, _ in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            # The database's own sort key, so the next page starts exactly after it.
            last, _, last_key = rows[limit - 1]
            next_cursor = encode_cursor(last_key, str(last.id))
        return WorkspacePage(items=items, next_cursor=next_cursor)

    def get(self, user: User, workspace_id: uuid.UUID) -> Workspace:
        with Session(self._engine) as db:
            record, scope = authorized(db, user, Action.VIEW_WORKSPACE, workspace_id)
            return workspace_view(user, record, scope.role)

    def owner_ids(self, workspace_id: uuid.UUID) -> list[uuid.UUID]:
        """The owners of the Workspace, for a system action that tells them something
        (e.g. a request that needs an owner). Authorizes nothing: callers have."""
        with Session(self._engine) as db:
            return list(
                db.scalars(
                    sa.select(MemberRecord.user_id).where(
                        MemberRecord.workspace_id == workspace_id, MemberRecord.role == "owner"
                    )
                )
            )

    def reviewer_ids(self, workspace_id: uuid.UUID) -> list[uuid.UUID]:
        """The owners and editors of the Workspace: who reviews what DAWAM proposes (e.g. a
        sync Change Set). Authorizes nothing: callers have."""
        with Session(self._engine) as db:
            return list(
                db.scalars(
                    sa.select(MemberRecord.user_id).where(
                        MemberRecord.workspace_id == workspace_id,
                        MemberRecord.role.in_(("owner", "editor")),
                    )
                )
            )

    def member_ids_among(
        self, workspace_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
    ) -> set[uuid.UUID]:
        """The users among ``user_ids`` who are members of the Workspace, for a module
        that must check ids a client sent (e.g. @mentions). Authorizes nothing."""
        ids = set(user_ids)
        if not ids:
            return set()
        with Session(self._engine) as db:
            return set(
                db.scalars(
                    sa.select(MemberRecord.user_id).where(
                        MemberRecord.workspace_id == workspace_id, MemberRecord.user_id.in_(ids)
                    )
                )
            )

    def names_by_id(self, workspace_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        """The names of the Workspaces among ``workspace_ids`` that exist. Authorizes
        nothing: for an admin-only view."""
        ids = set(workspace_ids)
        if not ids:
            return {}
        with Session(self._engine) as db:
            rows = db.execute(
                sa.select(WorkspaceRecord.id, WorkspaceRecord.name).where(
                    WorkspaceRecord.id.in_(ids)
                )
            )
            return {row.id: row.name for row in rows}

    def stage_progress(self, user: User, workspace_id: uuid.UUID) -> StageProgress:
        """Stage progress of a Workspace, for any member (viewers included)."""
        with Session(self._engine) as db:
            authorized(db, user, Action.VIEW_WORKSPACE, workspace_id)
        # No KPIs exist yet, so nothing there has been started.
        analysis = self._source_analysis(workspace_id) if self._source_analysis else []
        modeling = {
            m.layer: m.status
            for m in (self._modeling_progress(workspace_id) if self._modeling_progress else [])
        }
        return StageProgress(
            source_analysis=[SystemProgress(a.system_id, a.name, a.status) for a in analysis],
            kpis=KpiProgress(status="not_started"),
            dw_modeling=[
                LayerProgress(layer=layer, status=modeling.get(layer, "not_started"))
                for layer in LAYERS
            ],
        )

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        version: int,
        name: str | None = None,
        description: str | None = None,
        domain: str | None = None,
    ) -> Workspace:
        """Edit the Workspace's details (``None``: leave as is). ``version`` is the one
        the caller last saw; if the Workspace has changed since, raises ``ApiError``
        409 ``version_conflict`` and changes nothing."""
        with Session(self._engine) as db, db.begin():
            record, scope = authorized(db, user, Action.EDIT_WORKSPACE, workspace_id, lock=True)
            if record.version != version:
                raise _version_conflict(record.version)
            changed: list[str] = []
            if name is not None:
                cleaned = _clean(name, "name", NAME_MAX_LENGTH, required=True)
                if cleaned != record.name:
                    changed.append("name")
                record.name = cleaned
            if description is not None:
                cleaned = _clean(description, "description", DESCRIPTION_MAX_LENGTH)
                if cleaned != record.description:
                    changed.append("description")
                record.description = cleaned
            if domain is not None:
                cleaned = _clean(domain, "domain", DOMAIN_MAX_LENGTH)
                if cleaned != record.domain:
                    changed.append("domain")
                record.domain = cleaned
            record.version += 1
            record.updated_at = self._clock()
            if changed:
                record_activity(
                    db,
                    workspace_id=record.id,
                    actor_id=user.id,
                    verb="workspace.updated",
                    object_type="workspace",
                    object_id=record.id,
                    object_label=record.name,
                    details={"changed": changed},
                    at=record.updated_at,
                )
            db.flush()
            return workspace_view(user, record, scope.role)

    def archive(self, user: User, workspace_id: uuid.UUID) -> None:
        """Make the Workspace read-only: reads and exports keep working, every other
        action is refused by the policy until it is unarchived. For its owners and for
        admins, members or not. Raises ``ApiError`` 409 ``workspace_archived`` if it
        already is."""
        with Session(self._engine) as db, db.begin():
            record, _ = authorized(db, user, Action.ARCHIVE_WORKSPACE, workspace_id, lock=True)
            now = self._clock()
            record.status = "archived"
            record.archived_at = now
            record.updated_at = now
            record.version += 1
            record_activity(
                db,
                workspace_id=record.id,
                actor_id=user.id,
                verb="workspace.archived",
                object_type="workspace",
                object_id=record.id,
                object_label=record.name,
                at=now,
            )
            if self._on_archived is not None:
                self._on_archived(db, record.id, now)

    def unarchive(self, user: User, workspace_id: uuid.UUID) -> None:
        """Make an archived Workspace editable again. Raises ``ApiError`` 409
        ``workspace_not_archived`` if it is not archived."""
        with Session(self._engine) as db, db.begin():
            record, _ = authorized(db, user, Action.UNARCHIVE_WORKSPACE, workspace_id, lock=True)
            now = self._clock()
            record.status = "active"
            record.archived_at = None
            record.updated_at = now
            record.version += 1
            record_activity(
                db,
                workspace_id=record.id,
                actor_id=user.id,
                verb="workspace.unarchived",
                object_type="workspace",
                object_id=record.id,
                object_label=record.name,
                at=now,
            )

    def delete(
        self, user: User, workspace_id: uuid.UUID, *, name: str, ip: str | None = None
    ) -> None:
        """Delete the Workspace and everything in it, for good, if ``name`` is its name
        exactly. Owners may at any time; an admin who is not a member only once it is
        archived (409 ``workspace_not_archived`` before). Raises ``ApiError`` 422
        ``name_mismatch``. Writes a ``workspace_deleted`` security event."""
        if self._events is None:
            raise RuntimeError("deleting a Workspace must be recorded: pass events")
        with Session(self._engine) as db, db.begin():
            record, scope = authorized(db, user, Action.DELETE_WORKSPACE, workspace_id, lock=True)
            if name != record.name:
                raise ApiError(
                    422,
                    "name_mismatch",
                    "Type the Workspace's name exactly to delete it.",
                    {"field": "name"},
                )
            self._events.record(
                "workspace_deleted",
                actor_id=user.id,
                target_type="workspace",
                target_id=record.id,
                metadata={
                    "name": record.name,
                    "was_archived": record.status == "archived",
                    "as_member": scope.role is not None,
                },
                ip=ip,
                db=db,
            )
            db.delete(record)

    def add_member(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        member_id: uuid.UUID,
        role: WorkspaceRole,
    ) -> None:
        """Make the user ``member_id`` a member of the Workspace with ``role``, acting
        as ``user``. Raises ``ApiError`` 409 ``already_member``, 404 ``user_not_found``."""
        as_role(role)
        try:
            with Session(self._engine) as db, db.begin():
                authorized(db, user, Action.MANAGE_MEMBERS, workspace_id, lock=True)
                now = self._clock()
                db.add(
                    MemberRecord(
                        workspace_id=workspace_id,
                        user_id=member_id,
                        role=role,
                        added_by=user.id,
                        added_at=now,
                    )
                )
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="member.added",
                    object_type="user",
                    object_id=member_id,
                    details={"role": role},
                    at=now,
                )
                db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == MEMBER_PRIMARY_KEY:
                raise ApiError(
                    409, "already_member", "That user is already a member of this Workspace."
                ) from None
            if constraint == "fk_workspace_members_user_id_users":
                raise ApiError(404, "user_not_found", "No user has that id.") from None
            raise
