from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor
from dawam.platform.storage import FileStorage, new_key

from .internal.content import Unsupported, detect, extract_text
from .tables import PATH_MAX_LENGTH, WorkspaceFileRecord

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class WorkspaceFile:
    id: uuid.UUID
    workspace_id: uuid.UUID
    owner_kind: str
    owner_id: uuid.UUID
    name: str
    kind: str
    mime: str
    size: int
    text_status: str
    updated_by: uuid.UUID | None
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceFilePage:
    items: list[WorkspaceFile]
    next_cursor: str | None


@dataclass(frozen=True)
class FileContent:
    name: str
    mime: str
    data: bytes


def _view(record: WorkspaceFileRecord) -> WorkspaceFile:
    return WorkspaceFile(
        id=record.id,
        workspace_id=record.workspace_id,
        owner_kind=record.owner_kind,
        owner_id=record.owner_id,
        name=record.path,
        kind=record.kind,
        mime=record.mime,
        size=record.size,
        text_status=record.text_status,
        updated_by=record.updated_by,
        updated_at=record.updated_at,
    )


def clean_file_name(filename: str) -> str:
    """The name to keep for an upload: the last path segment (browsers and scripts may
    send a whole path), without control characters. Raises 422 ``invalid_file_name``."""
    name = _CONTROL_CHARACTERS.sub("", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    if not name or name in (".", "..") or len(name) > PATH_MAX_LENGTH:
        raise ApiError(
            422,
            "invalid_file_name",
            f"The file needs a name of 1 to {PATH_MAX_LENGTH} characters.",
        )
    return name


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """All of ``stream``, or 413 ``file_too_large`` if it is longer than ``max_bytes``."""
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(
            413,
            "file_too_large",
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.",
            {"max_bytes": max_bytes},
        )
    return data


class FileService:
    """Files in a Workspace's file areas. Every method authorizes through the workspaces
    module's policy first, so callers add no checks of their own."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        systems: SourceSystemService,
        storage: FileStorage,
        clock: Clock,
        max_upload_bytes: int,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._systems = systems
        self._storage = storage
        self._clock = clock
        self.max_upload_bytes = max_upload_bytes

    def upload(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        filename: str,
        content: BinaryIO,
    ) -> WorkspaceFile:
        """Put a file in a Source System's file area (owners and editors); its text is
        extracted for search. A file with the same name there is overwritten in place
        (spec §6.17). 404 for a system not in the Workspace, 413 ``file_too_large``,
        415 ``unsupported_file_type``, 422 ``invalid_file_name``."""
        self._workspaces.authorize(user, Action.UPLOAD_FILE, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        name = clean_file_name(filename)
        data = read_limited(content, self.max_upload_bytes)
        try:
            file_type = detect(name, data)
        except Unsupported as exc:
            raise ApiError(415, "unsupported_file_type", str(exc)) from None
        text, text_status = extract_text(file_type, data)

        key = new_key()
        self._storage.put(key, data)
        replaced_key: str | None = None
        try:
            with Session(self._engine) as db, db.begin():
                record = db.scalars(
                    sa.select(WorkspaceFileRecord)
                    .where(
                        WorkspaceFileRecord.workspace_id == workspace_id,
                        WorkspaceFileRecord.owner_kind == "source_system",
                        WorkspaceFileRecord.owner_id == system.id,
                        WorkspaceFileRecord.path == name,
                    )
                    .with_for_update()
                ).first()
                replacing = record is not None
                if record is None:
                    record = WorkspaceFileRecord(
                        id=uuid.uuid4(),
                        workspace_id=workspace_id,
                        owner_kind="source_system",
                        owner_id=system.id,
                        path=name,
                    )
                    db.add(record)
                else:
                    replaced_key = record.storage_key
                record.kind = "uploaded"
                record.mime = file_type.mime
                record.storage_key = key
                record.size = len(data)
                record.extracted_text = text
                record.text_status = text_status
                record.updated_by = user.id
                record.updated_at = self._clock()
                db.flush()
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="file.replaced" if replacing else "file.uploaded",
                    object_type="file",
                    object_id=record.id,
                    object_label=name,
                    details={"system_code": system.code, "size": len(data)},
                    at=record.updated_at,
                )
                view = _view(record)
        except IntegrityError:
            self._storage.delete(key)
            raise ApiError(
                409, "file_conflict", "Someone uploaded a file with that name at the same time."
            ) from None
        except BaseException:
            self._storage.delete(key)
            raise
        if replaced_key is not None:
            self._storage.delete(replaced_key)
        return view

    def list_for_system(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> WorkspaceFilePage:
        """The files of a Source System's file area, any member, ordered by name."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        system = self._systems.get(user, workspace_id, system_id)
        query = (
            sa.select(WorkspaceFileRecord)
            .where(
                WorkspaceFileRecord.workspace_id == workspace_id,
                WorkspaceFileRecord.owner_kind == "source_system",
                WorkspaceFileRecord.owner_id == system.id,
            )
            .order_by(WorkspaceFileRecord.path)
            .limit(limit + 1)
        )
        if cursor is not None:
            (after,) = decode_cursor(cursor, 1)
            query = query.where(WorkspaceFileRecord.path > after)
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        next_cursor = encode_cursor(records[limit - 1].path) if len(records) > limit else None
        return WorkspaceFilePage(items=[_view(r) for r in records[:limit]], next_cursor=next_cursor)

    def download(self, user: User, workspace_id: uuid.UUID, file_id: uuid.UUID) -> FileContent:
        """A file's bytes, any member. A file of another Workspace is as missing as one
        that does not exist: 404 ``not_found``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = db.get(WorkspaceFileRecord, file_id)
            if record is None or record.workspace_id != workspace_id:
                raise _not_found()
            name, mime, key = record.path, record.mime, record.storage_key
        try:
            data = self._storage.get(key)
        except KeyError:
            raise _not_found() from None
        return FileContent(name=name, mime=mime, data=data)


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "File not found.")
