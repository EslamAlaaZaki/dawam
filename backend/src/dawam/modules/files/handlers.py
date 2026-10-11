"""Text files as Change Set items (spec §6.17, story 146).

``FileContentHandler`` is the ``changesets`` engine's handler for ``file`` objects: an item
updates the text of an existing text file. The engine decides staleness (the file's text now
against the text the proposal saw), role and audit; this class checks the new text and
writes it. The bytes go to a fresh storage key so a rolled-back accept leaves the file as it
was (the old key is left behind rather than risk deleting what a rollback restores).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import AppliedChange
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError
from dawam.platform.storage import FileStorage, new_key

from .internal.content import TEXT_TYPES, Unsupported, detect, extract_text
from .tables import WorkspaceFileRecord

FIELDS = ("content",)
_TEXT_MIMES = {t.mime for t in TEXT_TYPES}


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


class FileContentHandler:
    object_type = "file"

    def __init__(self, storage: FileStorage, *, max_bytes: int) -> None:
        self._storage = storage
        self._max_bytes = max_bytes

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("Change Sets only update the text of existing files.")
        return Action.UPLOAD_FILE

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if set(payload) != set(FIELDS) or not isinstance(payload["content"], str):
            raise _invalid("A file item changes `content`, the file's new text.")
        record = self._record(db, workspace_id, object_id)
        if record is None:
            raise _invalid("The file does not exist.")
        if record.mime not in _TEXT_MIMES:
            raise _invalid("Only text files can be updated.")
        self._encode(record.path, payload["content"])

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        return None if record is None else {"content": self._text(record)}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The file no longer exists.")
        old_size = record.size
        data = self._encode(record.path, payload["content"])
        file_type = detect(record.path, data)
        text, text_status = extract_text(file_type, data)
        key = new_key()
        self._storage.put(key, data)
        record.mime = file_type.mime
        record.storage_key = key
        record.size = len(data)
        record.extracted_text = text
        record.text_status = text_status
        record.updated_at = at
        db.flush()
        return AppliedChange(
            entity_type="file",
            entity_id=record.id,
            old={"size": old_size},
            new={"size": len(data)},
            label=record.path,
        )

    def _encode(self, name: str, content: str) -> bytes:
        data = content.encode()
        if len(data) > self._max_bytes:
            raise _invalid("The new text is larger than the file size limit.")
        try:
            detect(name, data)
        except Unsupported as exc:
            raise _invalid(str(exc)) from None
        return data

    def _text(self, record: WorkspaceFileRecord) -> str:
        try:
            return self._storage.get(record.storage_key).decode("utf-8-sig", errors="replace")
        except KeyError:
            raise ApiError(404, "not_found", "The file's content is missing.") from None

    def _record(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID | None,
        *,
        lock: bool = False,
    ) -> WorkspaceFileRecord | None:
        if object_id is None:
            return None
        query = sa.select(WorkspaceFileRecord).where(
            WorkspaceFileRecord.id == object_id, WorkspaceFileRecord.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        return db.scalars(query).first()
