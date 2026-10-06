from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .tables import (
    CODE_CONSTRAINT,
    CODE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    NAME_MAX_LENGTH,
    OWNER_MAX_LENGTH,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

SYSTEM_CODE_PATTERN = re.compile(rf"[a-z][a-z0-9_]{{0,{CODE_MAX_LENGTH - 1}}}")
"""Identifier-safe: it goes into generated names (``stg_<system code>_...``), so it is
lowercase letters, digits and underscores, starting with a letter."""


@dataclass(frozen=True)
class SourceSystem:
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    code: str
    description: str
    business_owner: str
    technical_owner: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceSystemPage:
    items: list[SourceSystem]
    next_cursor: str | None
    """Pass it back as ``cursor`` for the next page; ``None`` on the last page."""


def _view(record: SourceSystemRecord) -> SourceSystem:
    return SourceSystem(
        id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        code=record.code,
        description=record.description,
        business_owner=record.business_owner,
        technical_owner=record.technical_owner,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Source System not found.")


def _clean(value: str, field: str, max_length: int, *, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise ApiError(
            422, "invalid_source_system", f"The {field} must not be empty.", {"field": field}
        )
    if len(cleaned) > max_length:
        raise ApiError(
            422,
            "invalid_source_system",
            f"The {field} must be at most {max_length} characters.",
            {"field": field},
        )
    return cleaned


def _clean_code(value: str) -> str:
    code = value.strip()
    if not SYSTEM_CODE_PATTERN.fullmatch(code):
        raise ApiError(
            422,
            "invalid_system_code",
            f"The System Code must be 1 to {CODE_MAX_LENGTH} characters: lowercase letters, "
            "digits and underscores, starting with a letter (e.g. cbs).",
            {"field": "code"},
        )
    return code


def _code_taken() -> ApiError:
    return ApiError(
        409, "system_code_taken", "Another Source System in this Workspace has that System Code."
    )


class SourceSystemService:
    """Source Systems of a Workspace. Every method that acts for a user authorizes
    through the workspaces module's policy first; callers add no checks of their own."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def create(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        name: str,
        code: str,
        description: str = "",
        business_owner: str = "",
        technical_owner: str = "",
    ) -> SourceSystem:
        """Add a Source System. 422 ``invalid_system_code`` / ``invalid_source_system``,
        409 ``system_code_taken``."""
        self._workspaces.authorize(user, Action.CREATE_SOURCE_SYSTEM, workspace_id)
        now = self._clock()
        record = SourceSystemRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=_clean(name, "name", NAME_MAX_LENGTH, required=True),
            code=_clean_code(code),
            description=_clean(description, "description", DESCRIPTION_MAX_LENGTH),
            business_owner=_clean(business_owner, "business owner", OWNER_MAX_LENGTH),
            technical_owner=_clean(technical_owner, "technical owner", OWNER_MAX_LENGTH),
            status="present",
            created_by=user.id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        try:
            with Session(self._engine) as db, db.begin():
                db.add(record)
                db.flush()
                self._record_activity(db, user, "source_system.created", record)
                return _view(record)
        except IntegrityError as exc:
            raise _integrity_error(exc) from None

    def list(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> SourceSystemPage:
        """The Workspace's Source Systems, any member, ordered by System Code."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        query = (
            sa.select(SourceSystemRecord)
            .where(
                SourceSystemRecord.workspace_id == workspace_id,
                SourceSystemRecord.status == "present",
            )
            .order_by(SourceSystemRecord.code)
            .limit(limit + 1)
        )
        if cursor is not None:
            (after_code,) = decode_cursor(cursor, 1)
            query = query.where(SourceSystemRecord.code > after_code)
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        next_cursor = encode_cursor(records[limit - 1].code) if len(records) > limit else None
        return SourceSystemPage(items=[_view(r) for r in records[:limit]], next_cursor=next_cursor)

    def get(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> SourceSystem:
        """Open a Source System, any member. 404 if it is not in ``workspace_id``."""
        # Authorize on the URL's Workspace first: a non-member gets the Workspace's 404
        # whether or not the system exists. ``_load`` then ties the system to it.
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return _view(self._load(db, workspace_id, system_id))

    def source_object_system(
        self, user: User, workspace_id: uuid.UUID, object_type: str, object_id: uuid.UUID
    ) -> uuid.UUID:
        """The Source System a table (``object_type="table"``) or column (``"column"``)
        belongs to, any member. 404 ``not_found`` if it is not in ``workspace_id``, as
        for one that does not exist."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        query = sa.select(SrcDbSchemaRecord.source_system_id).join(
            SrcTableRecord, SrcTableRecord.db_schema_id == SrcDbSchemaRecord.id
        )
        if object_type == "table":
            query = query.where(SrcTableRecord.id == object_id)
        elif object_type == "column":
            query = query.join(SrcColumnRecord, SrcColumnRecord.table_id == SrcTableRecord.id)
            query = query.where(SrcColumnRecord.id == object_id)
        else:
            raise ValueError(f"unknown source object type {object_type!r}")
        with Session(self._engine) as db:
            system_id = db.scalar(query)
            if system_id is None:
                raise ApiError(404, "not_found", "Table or column not found.")
            self._load(db, workspace_id, system_id)
            return system_id

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        version: int,
        name: str | None = None,
        code: str | None = None,
        description: str | None = None,
        business_owner: str | None = None,
        technical_owner: str | None = None,
    ) -> SourceSystem:
        """Edit a Source System (``None``: leave as is). Changing the System Code needs
        an owner (403 ``forbidden`` for an editor). A stale ``version`` is 409
        ``version_conflict``."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_SYSTEM, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                record = self._load(db, workspace_id, system_id, lock=True)
                # Before the code check: a stale form (its code is out of date too) is a
                # conflict, not a refusal.
                if record.version != version:
                    raise ApiError(
                        409,
                        "version_conflict",
                        "Someone else changed this Source System since you loaded it. "
                        "Reload and try again.",
                        {"current_version": record.version},
                    )
                new_code = _clean_code(code) if code is not None else record.code
                code_changes = new_code != record.code
                if code_changes:
                    self._workspaces.authorize(user, Action.CHANGE_SYSTEM_CODE, record.workspace_id)
                if name is not None:
                    record.name = _clean(name, "name", NAME_MAX_LENGTH, required=True)
                if description is not None:
                    record.description = _clean(description, "description", DESCRIPTION_MAX_LENGTH)
                if business_owner is not None:
                    record.business_owner = _clean(
                        business_owner, "business owner", OWNER_MAX_LENGTH
                    )
                if technical_owner is not None:
                    record.technical_owner = _clean(
                        technical_owner, "technical owner", OWNER_MAX_LENGTH
                    )
                if code_changes:
                    # Staging does not exist yet; once it does, this is where the rename
                    # Change Set for every affected Staging Table is produced (spec story
                    # 39). Until then the change applies directly.
                    record.code = new_code
                record.version += 1
                record.updated_at = self._clock()
                db.flush()
                self._record_activity(
                    db,
                    user,
                    "source_system.code_changed" if code_changes else "source_system.edited",
                    record,
                )
                return _view(record)
        except IntegrityError as exc:
            raise _integrity_error(exc) from None

    def _load(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> SourceSystemRecord:
        query = sa.select(SourceSystemRecord).where(
            SourceSystemRecord.id == system_id, SourceSystemRecord.status == "present"
        )
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        # A system under another Workspace's URL is as missing as one that does not
        # exist: no one learns where it lives.
        if record is None or record.workspace_id != workspace_id:
            raise _not_found()
        return record

    def _record_activity(
        self, db: Session, user: User, verb: str, record: SourceSystemRecord
    ) -> None:
        record_activity(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            verb=verb,
            object_type="source_system",
            object_id=record.id,
            object_label=record.name,
            details={"code": record.code},
            at=self._clock(),
        )


def _integrity_error(exc: IntegrityError) -> Exception:
    if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == CODE_CONSTRAINT:
        return _code_taken()
    return exc
