"""Renamed Source Objects: the service API (spec story 52a).

Extraction proposes rename candidates for removed objects that look like added ones
(``internal/renames``). An owner or editor confirms one, so the removed object keeps its
identity, descriptions and mappings under the new name, or rejects it, which leaves the
removal and the addition. ``merge`` does the same for a rename noticed late, with no
candidate. Confirmed renames and merges are audited and appear in the activity feed.
Any member lists the open candidates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .internal.renames import MergeError, merge_objects
from .tables import (
    RenameCandidateRecord,
    SnapshotRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

_ENTITY_TYPES = {
    "db_schema": "source_db_schema",
    "table": "source_table",
    "column": "source_column",
}


@dataclass(frozen=True)
class RenameCandidate:
    id: uuid.UUID
    snapshot_id: uuid.UUID
    object_type: str
    """``db_schema``, ``table`` or ``column``."""
    old_object_id: uuid.UUID
    old_name: str
    location: str | None
    """Where the object is: a table's Database Schema, a column's ``schema.table``."""
    new_object_id: uuid.UUID
    new_name: str
    confidence: float
    status: str
    """``suggested``, ``confirmed`` or ``rejected``."""


@dataclass(frozen=True)
class RenamedObject:
    object_type: str
    id: uuid.UUID
    """The surviving Source Object: the one that was removed."""
    name: str
    previous_name: str


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class RenameService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def list_candidates(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> list[RenameCandidate]:
        """The open (``suggested``) candidates of the latest Snapshot, most confident first
        (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            records = db.scalars(
                sa.select(RenameCandidateRecord)
                .join(SnapshotRecord, SnapshotRecord.id == RenameCandidateRecord.snapshot_id)
                .where(
                    SnapshotRecord.source_system_id == system_id,
                    SnapshotRecord.is_latest,
                    RenameCandidateRecord.status == "suggested",
                )
                .order_by(RenameCandidateRecord.confidence.desc(), RenameCandidateRecord.new_name)
            )
            return [view for record in records if (view := self._view(db, record)) is not None]

    def confirm(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> RenamedObject:
        """Confirm a suggested rename: the removed object takes the added one's place and
        name and keeps its identity. 409 ``rename_conflict`` if either object changed since
        it was suggested."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._lock_system(db, workspace_id, system_id)
            candidate = self._open_candidate(db, system_id, candidate_id)
            candidate.status = "confirmed"
            db.flush()
            return self._merge(
                db,
                user,
                system,
                candidate.object_type,
                candidate.old_object_id,
                candidate.new_object_id,
                candidate_id=candidate.id,
            )

    def reject(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> None:
        """Reject a suggested rename: the removal and the addition stand."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            self._lock_system(db, workspace_id, system_id)
            self._open_candidate(db, system_id, candidate_id).status = "rejected"

    def merge(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        object_type: str,
        removed_id: uuid.UUID,
        added_id: uuid.UUID,
    ) -> RenamedObject:
        """Merge a removed object into an added one of the same kind, for a rename noticed
        late. 409 ``rename_conflict`` if they are not such a pair."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._lock_system(db, workspace_id, system_id)
            return self._merge(db, user, system, object_type, removed_id, added_id)

    def _merge(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        object_type: str,
        removed_id: uuid.UUID,
        added_id: uuid.UUID,
        candidate_id: uuid.UUID | None = None,
    ) -> RenamedObject:
        try:
            survivor, previous_name = merge_objects(
                db, system.id, object_type, removed_id, added_id
            )
        except MergeError as error:
            raise ApiError(409, "rename_conflict", str(error)) from error
        entity_type = _ENTITY_TYPES[object_type]
        now = self._clock()
        record_audit(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            entity_type=entity_type,
            entity_id=survivor.id,
            old={"name": previous_name},
            new={"name": survivor.name, "merged_object_id": str(added_id)},
            at=now,
        )
        record_activity(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            verb=f"{entity_type}.renamed",
            object_type=entity_type,
            object_id=survivor.id,
            object_label=survivor.name,
            details={
                "source_system_id": str(system.id),
                "previous_name": previous_name,
                "rename_candidate_id": None if candidate_id is None else str(candidate_id),
            },
            at=now,
        )
        return RenamedObject(object_type, survivor.id, survivor.name, previous_name)

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        system = db.get(SourceSystemRecord, system_id)
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    def _lock_system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        """The Source System with its row locked, so a merge and an extraction never
        interleave."""
        system = db.scalars(
            sa.select(SourceSystemRecord)
            .where(SourceSystemRecord.id == system_id)
            .with_for_update()
        ).first()
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    def _open_candidate(
        self, db: Session, system_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> RenameCandidateRecord:
        candidate = db.scalars(
            sa.select(RenameCandidateRecord)
            .join(SnapshotRecord, SnapshotRecord.id == RenameCandidateRecord.snapshot_id)
            .where(
                RenameCandidateRecord.id == candidate_id,
                SnapshotRecord.source_system_id == system_id,
            )
            .with_for_update(of=RenameCandidateRecord)
        ).first()
        if candidate is None:
            raise _not_found("Rename candidate")
        if candidate.status != "suggested":
            raise ApiError(409, "rename_conflict", f"This rename was already {candidate.status}.")
        return candidate

    def _view(self, db: Session, record: RenameCandidateRecord) -> RenameCandidate | None:
        old: Any
        location: str | None = None
        if record.object_type == "db_schema":
            old = db.get(SrcDbSchemaRecord, record.old_object_id)
        elif record.object_type == "table":
            old = db.get(SrcTableRecord, record.old_object_id)
            if old is not None:
                location = db.get(SrcDbSchemaRecord, old.db_schema_id).name  # type: ignore[union-attr]
        else:
            old = db.get(SrcColumnRecord, record.old_object_id)
            if old is not None:
                table = db.get(SrcTableRecord, old.table_id)
                schema = db.get(SrcDbSchemaRecord, table.db_schema_id)  # type: ignore[union-attr]
                location = f"{schema.name}.{table.name}"  # type: ignore[union-attr]
        if old is None:
            return None
        return RenameCandidate(
            id=record.id,
            snapshot_id=record.snapshot_id,
            object_type=record.object_type,
            old_object_id=record.old_object_id,
            old_name=old.name,
            location=location,
            new_object_id=record.new_object_id,
            new_name=record.new_name,
            confidence=record.confidence,
            status=record.status,
        )
