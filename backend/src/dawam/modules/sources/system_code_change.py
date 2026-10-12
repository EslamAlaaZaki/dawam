"""A System Code change as a Change Set item (spec §6.7, story 39).

``SourceSystemCodeHandler`` is the ``changesets`` engine's handler for ``source_system``
objects: an item changes the System Code. It needs the owner role (the permission matrix's
"change a System Code"). The warehouse module proposes it together with the renames of every
affected Staging Table, so the code and the names change in one accept.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.changesets import AppliedChange
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .service import SYSTEM_CODE_PATTERN
from .tables import SourceSystemRecord

SOURCE_SYSTEM = "source_system"


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


class SourceSystemCodeHandler:
    object_type = SOURCE_SYSTEM
    wants_actor = True

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("Change Sets only change a Source System's System Code.")
        return Action.CHANGE_SYSTEM_CODE

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        code = payload.get("code")
        if set(payload) != {"code"} or not isinstance(code, str):
            raise _invalid("A source_system item changes only its `code`.")
        if not SYSTEM_CODE_PATTERN.fullmatch(code):
            raise _invalid(f"`{code}` is not a valid System Code.")
        if self._record(db, workspace_id, object_id) is None:
            raise _invalid("The Source System does not exist.")
        if self._taken(db, workspace_id, object_id, code):
            raise _invalid(f"Another Source System already has the System Code `{code}`.")

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        return None if record is None else {f: getattr(record, f) for f in fields}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The Source System no longer exists.")
        code = payload["code"]
        if self._taken(db, workspace_id, record.id, code):
            raise ApiError(
                409, "system_code_taken", "Another Source System has that System Code now."
            )
        old = record.code
        record.code = code
        record.version += 1
        record.updated_at = at
        db.flush()
        record_activity(
            db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            verb="source_system.code_changed",
            object_type="source_system",
            object_id=record.id,
            object_label=record.name,
            details={"code": code},
            at=at,
        )
        return AppliedChange(SOURCE_SYSTEM, record.id, {"code": old}, {"code": code}, record.name)

    @staticmethod
    def _record(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> SourceSystemRecord | None:
        if object_id is None:
            return None
        query = sa.select(SourceSystemRecord).where(
            SourceSystemRecord.id == object_id,
            SourceSystemRecord.workspace_id == workspace_id,
            SourceSystemRecord.status == "present",
        )
        if lock:
            query = query.with_for_update()
        return db.scalars(query).first()

    @staticmethod
    def _taken(db: Session, workspace_id: uuid.UUID, own_id: uuid.UUID | None, code: str) -> bool:
        return (
            db.scalar(
                sa.select(SourceSystemRecord.id).where(
                    SourceSystemRecord.workspace_id == workspace_id,
                    SourceSystemRecord.code == code,
                    SourceSystemRecord.id != own_id,
                )
            )
            is not None
        )
