from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .service import WorkspaceFile, WorkspaceFilePage
from .service import _view as _file_view
from .tables import (
    LINK_KINDS,
    NOTE_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    URL_MAX_LENGTH,
    FileObjectLinkRecord,
    SourceLinkRecord,
    WorkspaceFileRecord,
)


@dataclass(frozen=True)
class SourceLink:
    id: uuid.UUID
    source_system_id: uuid.UUID
    kind: str
    title: str
    url: str
    note: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceLinkPage:
    items: list[SourceLink]
    next_cursor: str | None


@dataclass(frozen=True)
class FileObjectLink:
    file_id: uuid.UUID
    object_type: str
    object_id: uuid.UUID
    created_at: datetime


def _link_view(record: SourceLinkRecord) -> SourceLink:
    return SourceLink(
        id=record.id,
        source_system_id=record.source_system_id,
        kind=record.kind,
        title=record.title,
        url=record.url,
        note=record.note,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _object_link_view(record: FileObjectLinkRecord) -> FileObjectLink:
    return FileObjectLink(
        file_id=record.file_id,
        object_type=record.object_type,
        object_id=record.object_id,
        created_at=record.created_at,
    )


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_source_link", message, {"field": field})


def _clean_text(value: str, field: str, max_length: int, *, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise _invalid(f"The {field} must not be empty.", field)
    if len(cleaned) > max_length:
        raise _invalid(f"The {field} must be at most {max_length} characters.", field)
    return cleaned


def _clean_kind(kind: str) -> str:
    if kind not in LINK_KINDS:
        raise _invalid(f"The kind must be one of {', '.join(LINK_KINDS)}.", "kind")
    return kind


def _clean_url(value: str) -> str:
    """An absolute ``http`` or ``https`` URL: anything else (``javascript:``, ...) would
    be a script when someone clicks it."""
    url = value.strip()
    if len(url) > URL_MAX_LENGTH:
        raise _invalid(f"The URL must be at most {URL_MAX_LENGTH} characters.", "url")
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        valid = False
    if not valid or any(c.isspace() or ord(c) < 32 for c in url):
        raise _invalid("The URL must be a full http:// or https:// address.", "url")
    return url


def _link_not_found() -> ApiError:
    return ApiError(404, "not_found", "Link not found.")


def _file_not_found() -> ApiError:
    return ApiError(404, "not_found", "File not found.")


class LinkService:
    """External links of Source Systems and links from documents to tables and columns.
    Every method authorizes through the workspaces module's policy first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        systems: SourceSystemService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._systems = systems
        self._clock = clock

    # -- Source links (story 65) ----------------------------------------------------------

    def create_link(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        kind: str,
        title: str,
        url: str,
        note: str = "",
    ) -> SourceLink:
        """Link a repository, Jira issue, Confluence page or other page to a Source System
        (owners and editors). 422 ``invalid_source_link``."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_SYSTEM, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        now = self._clock()
        record = SourceLinkRecord(
            id=uuid.uuid4(),
            source_system_id=system.id,
            kind=_clean_kind(kind),
            title=_clean_text(title, "title", TITLE_MAX_LENGTH, required=True),
            url=_clean_url(url),
            note=_clean_text(note, "note", NOTE_MAX_LENGTH),
            created_by=user.id,
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            self._activity(db, user, workspace_id, "source_link.created", record, system.code)
            return _link_view(record)

    def list_links(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> SourceLinkPage:
        """A Source System's links, any member, ordered by title."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        query = (
            sa.select(SourceLinkRecord)
            .where(SourceLinkRecord.source_system_id == system.id)
            .order_by(SourceLinkRecord.title, SourceLinkRecord.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            after_title, after_id = decode_cursor(cursor, 2)
            try:
                after = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(
                    422, "invalid_cursor", "The cursor is not valid; start from the first page."
                ) from None
            query = query.where(
                sa.tuple_(SourceLinkRecord.title, SourceLinkRecord.id) > (after_title, after)
            )
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        last = records[limit - 1] if len(records) > limit else None
        next_cursor = encode_cursor(last.title, str(last.id)) if last is not None else None
        return SourceLinkPage(
            items=[_link_view(r) for r in records[:limit]], next_cursor=next_cursor
        )

    def update_link(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        link_id: uuid.UUID,
        *,
        kind: str | None = None,
        title: str | None = None,
        url: str | None = None,
        note: str | None = None,
    ) -> SourceLink:
        """Edit a link (``None``: leave as is), owners and editors."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_SYSTEM, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        with Session(self._engine) as db, db.begin():
            record = self._load_link(db, system.id, link_id, lock=True)
            if kind is not None:
                record.kind = _clean_kind(kind)
            if title is not None:
                record.title = _clean_text(title, "title", TITLE_MAX_LENGTH, required=True)
            if url is not None:
                record.url = _clean_url(url)
            if note is not None:
                record.note = _clean_text(note, "note", NOTE_MAX_LENGTH)
            record.updated_at = self._clock()
            db.flush()
            self._activity(db, user, workspace_id, "source_link.edited", record, system.code)
            return _link_view(record)

    def delete_link(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, link_id: uuid.UUID
    ) -> None:
        """Remove a link, owners and editors."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_SYSTEM, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        with Session(self._engine) as db, db.begin():
            record = self._load_link(db, system.id, link_id, lock=True)
            self._activity(db, user, workspace_id, "source_link.deleted", record, system.code)
            db.delete(record)

    def _load_link(
        self, db: Session, system_id: uuid.UUID, link_id: uuid.UUID, *, lock: bool = False
    ) -> SourceLinkRecord:
        query = sa.select(SourceLinkRecord).where(
            SourceLinkRecord.id == link_id, SourceLinkRecord.source_system_id == system_id
        )
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        if record is None:
            raise _link_not_found()
        return record

    def _activity(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        verb: str,
        record: SourceLinkRecord,
        system_code: str,
    ) -> None:
        record_activity(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            verb=verb,
            object_type="source_link",
            object_id=record.id,
            object_label=record.title,
            details={"system_code": system_code, "kind": record.kind},
            at=self._clock(),
        )

    # -- Documents linked to tables and columns (story 66) --------------------------------

    def link_document(
        self,
        user: User,
        workspace_id: uuid.UUID,
        file_id: uuid.UUID,
        object_type: str,
        object_id: uuid.UUID,
    ) -> tuple[FileObjectLink, bool]:
        """Link a file to a table or column of the Workspace (owners and editors), so it
        shows on that page. Returns the link and whether it is new; linking twice is a
        no-op. 404 for a file or object not in the Workspace."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        self._systems.source_object_system(user, workspace_id, object_type, object_id)
        with Session(self._engine) as db, db.begin():
            file = self._load_file(db, workspace_id, file_id)
            existing = db.get(FileObjectLinkRecord, (file_id, object_type, object_id))
            if existing is not None:
                return _object_link_view(existing), False
            record = FileObjectLinkRecord(
                file_id=file_id,
                object_type=object_type,
                object_id=object_id,
                created_by=user.id,
                created_at=self._clock(),
            )
            db.add(record)
            db.flush()
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="file.linked",
                object_type="file",
                object_id=file.id,
                object_label=file.path,
                details={"object_type": object_type, "object_id": str(object_id)},
                at=record.created_at,
            )
            return _object_link_view(record), True

    def unlink_document(
        self,
        user: User,
        workspace_id: uuid.UUID,
        file_id: uuid.UUID,
        object_type: str,
        object_id: uuid.UUID,
    ) -> None:
        """Remove a file's link to a table or column (owners and editors). 404 if there
        is no such link."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        with Session(self._engine) as db, db.begin():
            file = self._load_file(db, workspace_id, file_id)
            record = db.get(FileObjectLinkRecord, (file_id, object_type, object_id))
            if record is None:
                raise ApiError(404, "not_found", "That file is not linked to that object.")
            db.delete(record)
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="file.unlinked",
                object_type="file",
                object_id=file.id,
                object_label=file.path,
                details={"object_type": object_type, "object_id": str(object_id)},
                at=self._clock(),
            )

    def links_of_document(
        self, user: User, workspace_id: uuid.UUID, file_id: uuid.UUID
    ) -> list[FileObjectLink]:
        """The tables and columns a file is linked to, any member."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_file(db, workspace_id, file_id)
            records = db.scalars(
                sa.select(FileObjectLinkRecord)
                .where(FileObjectLinkRecord.file_id == file_id)
                .order_by(
                    FileObjectLinkRecord.created_at,
                    FileObjectLinkRecord.object_type,
                    FileObjectLinkRecord.object_id,
                )
            )
            return [_object_link_view(r) for r in records]

    def documents_of_object(
        self,
        user: User,
        workspace_id: uuid.UUID,
        object_type: str,
        object_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> WorkspaceFilePage:
        """The files linked to a table or column, any member, ordered by name."""
        self._systems.source_object_system(user, workspace_id, object_type, object_id)
        query = (
            sa.select(WorkspaceFileRecord)
            .join(FileObjectLinkRecord, FileObjectLinkRecord.file_id == WorkspaceFileRecord.id)
            .where(
                WorkspaceFileRecord.workspace_id == workspace_id,
                FileObjectLinkRecord.object_type == object_type,
                FileObjectLinkRecord.object_id == object_id,
            )
            .order_by(WorkspaceFileRecord.path, WorkspaceFileRecord.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            after_path, after_id = decode_cursor(cursor, 2)
            try:
                after = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(
                    422, "invalid_cursor", "The cursor is not valid; start from the first page."
                ) from None
            query = query.where(
                sa.tuple_(WorkspaceFileRecord.path, WorkspaceFileRecord.id) > (after_path, after)
            )
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        last = records[limit - 1] if len(records) > limit else None
        next_cursor = encode_cursor(last.path, str(last.id)) if last is not None else None
        items: list[WorkspaceFile] = [_file_view(r) for r in records[:limit]]
        return WorkspaceFilePage(items=items, next_cursor=next_cursor)

    def _load_file(
        self, db: Session, workspace_id: uuid.UUID, file_id: uuid.UUID
    ) -> WorkspaceFileRecord:
        record = db.get(WorkspaceFileRecord, file_id)
        if record is None or record.workspace_id != workspace_id:
            raise _file_not_found()
        return record
