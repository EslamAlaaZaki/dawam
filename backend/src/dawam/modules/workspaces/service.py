from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
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
    NAME_MAX_LENGTH,
    MemberRecord,
    WorkspaceRecord,
)


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


def _role(value: str) -> WorkspaceRole:
    if value not in WORKSPACE_ROLES:
        raise ValueError(f"unknown Workspace role {value!r}")
    return value  # type: ignore[return-value]  # checked above


class WorkspaceService:
    """Workspaces and their members. Every method that acts for a user authorizes
    through the policy (``can``) first; callers add no checks of their own."""

    def __init__(self, engine: sa.Engine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

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
            _, scope = self._authorized(db, user, action, workspace_id)
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
            return self._workspace(user, record, "owner")

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
        items = [self._workspace(user, record, _role(role)) for record, role, _ in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            # The database's own sort key, so the next page starts exactly after it.
            last, _, last_key = rows[limit - 1]
            next_cursor = encode_cursor(last_key, str(last.id))
        return WorkspacePage(items=items, next_cursor=next_cursor)

    def get(self, user: User, workspace_id: uuid.UUID) -> Workspace:
        with Session(self._engine) as db:
            record, scope = self._authorized(db, user, Action.VIEW_WORKSPACE, workspace_id)
            return self._workspace(user, record, scope.role)

    def stage_progress(self, user: User, workspace_id: uuid.UUID) -> StageProgress:
        """Stage progress of a Workspace, for any member (viewers included)."""
        with Session(self._engine) as db:
            self._authorized(db, user, Action.VIEW_WORKSPACE, workspace_id)
        # No Source Systems, KPIs or DW Schema exist yet, so nothing has been started.
        return StageProgress(
            source_analysis=[],
            kpis=KpiProgress(status="not_started"),
            dw_modeling=[LayerProgress(layer=layer, status="not_started") for layer in LAYERS],
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
            record, scope = self._authorized(
                db, user, Action.EDIT_WORKSPACE, workspace_id, lock=True
            )
            if record.version != version:
                raise _version_conflict(record.version)
            if name is not None:
                record.name = _clean(name, "name", NAME_MAX_LENGTH, required=True)
            if description is not None:
                record.description = _clean(description, "description", DESCRIPTION_MAX_LENGTH)
            if domain is not None:
                record.domain = _clean(domain, "domain", DOMAIN_MAX_LENGTH)
            record.version += 1
            record.updated_at = self._clock()
            db.flush()
            return self._workspace(user, record, scope.role)

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
        _role(role)
        with Session(self._engine) as db:
            self._authorized(db, user, Action.MANAGE_MEMBERS, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                db.add(
                    MemberRecord(
                        workspace_id=workspace_id,
                        user_id=member_id,
                        role=role,
                        added_by=user.id,
                        added_at=self._clock(),
                    )
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "pk_workspace_members":
                raise ApiError(
                    409, "already_member", "That user is already a member of this Workspace."
                ) from None
            if constraint == "fk_workspace_members_user_id_users":
                raise ApiError(404, "user_not_found", "No user has that id.") from None
            raise

    def _authorized(
        self,
        db: Session,
        user: User,
        action: Action,
        workspace_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> tuple[WorkspaceRecord, WorkspaceScope]:
        query = (
            sa.select(WorkspaceRecord, MemberRecord.role)
            .outerjoin(
                MemberRecord,
                (MemberRecord.workspace_id == WorkspaceRecord.id)
                & (MemberRecord.user_id == user.id),
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
            role=_role(role) if role is not None else None,
        )
        if can(user, action, scope):
            return record, scope
        raise _forbidden() if scope.role is not None else _not_found()

    @staticmethod
    def _workspace(user: User, record: WorkspaceRecord, role: WorkspaceRole | None) -> Workspace:
        if role is None:
            # Only members get a Workspace; an admin-only action never returns one.
            raise ValueError("a Workspace is only shown to its members")
        scope = WorkspaceScope(workspace_id=record.id, user_id=user.id, role=role)
        return Workspace(
            id=record.id,
            name=record.name,
            description=record.description,
            domain=record.domain,
            role=role,
            permissions=frozenset(a for a in WORKSPACE_ACTIONS if can(user, a, scope)),
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
