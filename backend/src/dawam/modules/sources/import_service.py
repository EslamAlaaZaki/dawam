"""Schema Import: the service API (spec §6.4, stories 47, 49, 50, 51).

An owner or editor downloads the template, fills it, and uploads it. ``validate`` only
reports; ``upload`` validates and, if the report has no errors and either no warnings or
the caller accepted them, stores the catalog as a Snapshot with ``origin = import``
through the same writer extraction uses, so identity matching and the diff against the
previous Snapshot are the same whichever way the metadata came in. A file with errors
writes nothing.

Any member sees what an imported Source System cannot do (``status``). An editor asks
for a live Connection (``request_connection``); the owners are notified and an owner
supplies it on the Connection endpoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.hooks import SnapshotCreatedHook, notify_snapshot_created

from .internal.schema_import import ImportReport, build_template, read_catalog
from .internal.snapshots import store_catalog
from .snapshot_service import FOLDS_CASE, SnapshotSummary, _summary
from .tables import (
    ConnectionRecord,
    SnapshotRecord,
    SourceSystemRecord,
    SrcDbSchemaRecord,
)

DISABLED_FEATURES: tuple[str, ...] = (
    "Profiling (row, null and distinct counts, top values, min and max)",
    "Value-based PII scans",
    "Value-overlap relationship inference",
    "AI source queries (data exploration)",
)
"""What needs a live Connection (spec §6.4). Name-based PII, name/type-based inference and
routine JOIN parsing still work."""


@dataclass(frozen=True)
class ImportResult:
    report: ImportReport
    imported: bool
    """A Snapshot was saved, or the import matched the latest one (``unchanged``)."""
    unchanged: bool
    snapshot: SnapshotSummary | None


@dataclass(frozen=True)
class ImportStatus:
    has_connection: bool
    latest_origin: Literal["connection", "import"] | None
    imported: bool
    """The latest Snapshot came from an import (or the system has no Connection but has
    Snapshots), so the features in ``disabled_features`` do not work."""
    disabled_features: list[str]


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Source System not found.")


class SchemaImportService:
    """Every method authorizes through the workspaces policy."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        notifications: NotificationService | None = None,
        on_snapshot: SnapshotCreatedHook | None = None,
    ) -> None:
        self._on_snapshot = on_snapshot
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._notifications = notifications or NotificationService(engine, clock=clock)

    def template(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> bytes:
        """The Schema Import workbook (owners and editors)."""
        self._workspaces.authorize(user, Action.RUN_EXTRACTION, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
        return build_template()

    def validate(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        files: list[tuple[str, bytes]],
    ) -> ImportReport:
        """The validation report of an upload; nothing is saved (owners and editors)."""
        self._workspaces.authorize(user, Action.RUN_EXTRACTION, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
        return read_catalog(files)[1]

    def upload(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        files: list[tuple[str, bytes]],
        *,
        accept_warnings: bool = False,
        engine: str | None = None,
    ) -> ImportResult:
        """Validate and, if allowed, save an upload as a Snapshot (owners and editors).

        ``engine`` says whether names fold case when the Source System has no Connection
        to tell (one of ``postgresql``, ``sqlserver``, ``mysql``, ``oracle``); a
        Connection's engine wins."""
        self._workspaces.authorize(user, Action.RUN_EXTRACTION, workspace_id)
        if engine is not None and engine not in FOLDS_CASE:
            raise ApiError(422, "unsupported_engine", f"The engine {engine!r} is not supported.")
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
        catalog, report = read_catalog(files)
        if catalog is None or (report.warnings and not accept_warnings):
            return ImportResult(report, imported=False, unchanged=False, snapshot=None)
        with Session(self._engine) as db, db.begin():
            system = db.scalars(
                sa.select(SourceSystemRecord)
                .where(SourceSystemRecord.id == system_id)
                .with_for_update()
            ).one()
            if system.status != "present":
                raise _not_found()
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            engine_name = connection.engine if connection is not None else engine
            known = set(
                db.scalars(
                    sa.select(SrcDbSchemaRecord.name).where(
                        SrcDbSchemaRecord.source_system_id == system_id
                    )
                )
            )
            # An import states the whole database: a schema it lacks is gone, not out of
            # scope, unless a Connection limits the schemas that are looked at.
            allowed = (set(connection.allowed_schemas) if connection is not None else known) | set(
                catalog.schemas
            )
            snapshot = store_catalog(
                db,
                source_system_id=system_id,
                catalog=catalog,
                allowed_schemas=allowed,
                origin="import",
                job_id=None,
                taken_at=self._clock(),
                folds_case=FOLDS_CASE.get(engine_name or "", False),
            )
            if snapshot is None:
                return ImportResult(report, imported=True, unchanged=True, snapshot=None)
            record_activity(
                db,
                workspace_id=system.workspace_id,
                actor_id=user.id,
                verb="snapshot.created",
                object_type="snapshot",
                object_id=snapshot.id,
                object_label=system.name,
                details={"origin": "import", "tables": snapshot.table_count},
                at=snapshot.taken_at,
            )
            result = ImportResult(
                report, imported=True, unchanged=False, snapshot=_summary(snapshot)
            )
            workspace_for_hook = system.workspace_id
        notify_snapshot_created(self._on_snapshot, workspace_for_hook, system_id, user.id)
        return result

    def status(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> ImportStatus:
        """Whether the Source System works from imports, and what that disables (any
        member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            has_connection = (
                db.scalar(
                    sa.select(sa.func.count())
                    .select_from(ConnectionRecord)
                    .where(ConnectionRecord.source_system_id == system_id)
                )
                or 0
            ) > 0
            origin = db.scalar(
                sa.select(SnapshotRecord.origin).where(
                    SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
                )
            )
        imported = origin == "import" and not has_connection
        return ImportStatus(
            has_connection=has_connection,
            latest_origin=origin,
            imported=imported,
            disabled_features=list(DISABLED_FEATURES) if imported else [],
        )

    def request_connection(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> int:
        """Ask the owners to supply a live Connection (owners and editors); returns how
        many owners were notified. 409 ``connection_exists`` if there is one already."""
        self._workspaces.authorize(user, Action.RUN_EXTRACTION, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._load_system(db, workspace_id, system_id)
            has_connection = db.scalars(
                sa.select(ConnectionRecord.id).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            if has_connection is not None:
                raise ApiError(
                    409, "connection_exists", "This Source System already has a Connection."
                )
            owners = self._workspaces.owner_ids(workspace_id)
            count = self._notifications.notify(
                owners,
                kind="needs_owner",
                message=(
                    f"{user.display_name} asks for a live Connection for the Source System "
                    f"“{system.name}”, which is analysed from a Schema Import."
                ),
                workspace_id=workspace_id,
                ref_type="source_system",
                ref_id=system.id,
                db=db,
            )
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="connection.requested",
                object_type="source_system",
                object_id=system.id,
                object_label=system.name,
                details={},
                at=self._clock(),
            )
            return count

    def _load_system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        record = db.get(SourceSystemRecord, system_id)
        if record is None or record.status != "present" or record.workspace_id != workspace_id:
            raise _not_found()
        return record
