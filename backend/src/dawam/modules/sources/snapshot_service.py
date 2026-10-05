"""Metadata extraction into Snapshots: the service API (spec stories 45, 46, §6.3).

An owner or editor starts an extraction (``start_extraction``); it runs as an
``extract`` background job (``run_extraction`` is its handler) that reads the
Connection's allowed Database Schemas and stores the result as a new Snapshot, unless
nothing changed since the latest one. Any member reads Snapshots (``list`` and
``get``), archived Workspaces included.

The source is read outside any app-database transaction, so a slow source never holds a
row lock here; the Snapshot is then written in one transaction holding the Source
System's row lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import ConnectionParams, Connector, ConnectorError, connector_for
from .internal.snapshots import store_catalog
from .tables import (
    ConnectionRecord,
    DefinitionTextRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotDbSchemaRecord,
    SnapshotIndexRecord,
    SnapshotRecord,
    SnapshotRoutineRecord,
    SnapshotTableRecord,
    SourceSystemRecord,
)

EXTRACT_JOB = "extract"
FOLDS_CASE = {"postgresql": False}
"""Whether the engine folds identifier case, so names differing only in case may match
the same Source Object (PostgreSQL keeps quoted ``"Customer"`` and ``customer`` apart)."""


@dataclass(frozen=True)
class SnapshotSummary:
    id: uuid.UUID
    source_system_id: uuid.UUID
    origin: str
    """``connection`` or ``import``."""
    job_id: uuid.UUID | None
    taken_at: datetime
    is_latest: bool
    schema_count: int
    table_count: int
    column_count: int
    routine_count: int


@dataclass(frozen=True)
class SnapshotDbSchema:
    id: uuid.UUID
    """The Source Object (``SrcDbSchema``)."""
    name: str


@dataclass(frozen=True)
class SnapshotColumn:
    id: uuid.UUID
    """The Source Object (``SrcColumn``): the same in every Snapshot."""
    name: str
    ordinal: int
    data_type: str
    is_nullable: bool
    is_pk: bool
    default: str | None
    comment: str | None


@dataclass(frozen=True)
class SnapshotConstraint:
    name: str
    type: str
    """``pk``, ``fk`` or ``unique``."""
    columns: list[str]
    ref_table_id: uuid.UUID | None
    ref_db_schema: str | None
    ref_table: str | None
    ref_columns: list[str]


@dataclass(frozen=True)
class SnapshotIndex:
    name: str
    columns: list[str]
    is_unique: bool


@dataclass(frozen=True)
class SnapshotTable:
    id: uuid.UUID
    """The Source Object (``SrcTable``): the same in every Snapshot."""
    db_schema: str
    name: str
    kind: str
    """``table`` or ``view``."""
    view_definition: str | None
    row_estimate: int | None
    comment: str | None
    columns: list[SnapshotColumn]
    constraints: list[SnapshotConstraint]
    indexes: list[SnapshotIndex]


@dataclass(frozen=True)
class SnapshotRoutine:
    id: uuid.UUID
    """The Source Object (``SrcRoutine``)."""
    db_schema: str
    name: str
    kind: str
    """``procedure`` or ``function``."""
    signature: str
    definition: str | None


@dataclass(frozen=True)
class SnapshotContent:
    snapshot: SnapshotSummary
    db_schemas: list[SnapshotDbSchema]
    tables: list[SnapshotTable]
    routines: list[SnapshotRoutine]


def _summary(record: SnapshotRecord) -> SnapshotSummary:
    return SnapshotSummary(
        id=record.id,
        source_system_id=record.source_system_id,
        origin=record.origin,
        job_id=record.job_id,
        taken_at=record.taken_at,
        is_latest=record.is_latest,
        schema_count=record.schema_count,
        table_count=record.table_count,
        column_count=record.column_count,
        routine_count=record.routine_count,
    )


def _not_found(what: str = "Source System") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class ExtractionError(Exception):
    """Why an extraction job failed; its message is safe to show."""


class SnapshotService:
    """Every method that acts for a user authorizes through the workspaces policy."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        jobs: JobRunner,
        encryption_key: bytes,
        clock: Clock,
        connectors: Callable[[str, ConnectionParams], Connector] = connector_for,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._jobs = JobService(engine, runner=jobs, clock=clock)
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._connectors = connectors

    # -- for users ----------------------------------------------------------------------

    def start_extraction(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Job:
        """Queue an ``extract`` job for the Source System (owners and editors). 409
        ``connection_missing`` if it has no Connection."""
        self._workspaces.authorize(user, Action.RUN_EXTRACTION, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._load_system(db, workspace_id, system_id)
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            if connection is None:
                raise ApiError(
                    409,
                    "connection_missing",
                    "This Source System has no Connection: an owner adds one first.",
                )
            return self._jobs.submit(
                workspace_id,
                EXTRACT_JOB,
                {"source_system_id": str(system_id), "requested_by": str(user.id)},
                title=f"Extract metadata: {system.name}",
                created_by=user.id,
                db=db,
            )

    def list(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> list[SnapshotSummary]:
        """The Source System's Snapshots, newest first (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            records = db.scalars(
                sa.select(SnapshotRecord)
                .where(SnapshotRecord.source_system_id == system_id)
                .order_by(SnapshotRecord.taken_at.desc(), SnapshotRecord.id.desc())
            )
            return [_summary(r) for r in records]

    def get(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> SnapshotContent:
        """Everything one Snapshot captured (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            record = db.get(SnapshotRecord, snapshot_id)
            if record is None or record.source_system_id != system_id:
                raise _not_found("Snapshot")
            return self._content(db, record)

    # -- the job ------------------------------------------------------------------------

    def run_extraction(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``extract`` job handler: read the source, then store a Snapshot."""
        system_id = uuid.UUID(params["source_system_id"])
        requested_by = uuid.UUID(params["requested_by"]) if params.get("requested_by") else None
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            if system is None or system.status != "present" or connection is None:
                raise ExtractionError("The Source System or its Connection no longer exists.")
            engine_name = connection.engine
            allowed = tuple(connection.allowed_schemas)
            params_for_source = ConnectionParams(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=self._password(connection),
                allowed_schemas=allowed,
                options=dict(connection.options),
            )
        ctx.log(f"Reading metadata from the Database Schemas {', '.join(allowed)}.")
        ctx.progress(5)
        try:
            catalog = self._connectors(engine_name, params_for_source).extract()
        except ConnectorError as exc:
            raise ExtractionError(exc.message) from None
        columns = sum(len(t.columns) for t in catalog.tables)
        ctx.log(
            f"Read {len(catalog.schemas)} Database Schemas, {len(catalog.tables)} tables and "
            f"views, {columns} columns and {len(catalog.routines)} routines."
        )
        ctx.progress(60)
        ctx.raise_if_cancelled()
        with Session(self._engine) as db, db.begin():
            system = db.scalars(
                sa.select(SourceSystemRecord)
                .where(SourceSystemRecord.id == system_id)
                .with_for_update()
            ).one()
            snapshot = store_catalog(
                db,
                source_system_id=system_id,
                catalog=catalog,
                allowed_schemas=allowed,
                origin="connection",
                job_id=ctx.job_id,
                taken_at=self._clock(),
                folds_case=FOLDS_CASE.get(engine_name, False),
            )
            if snapshot is None:
                ctx.log("No differences from the previous Snapshot: no new Snapshot was created.")
                return
            record_activity(
                db,
                workspace_id=system.workspace_id,
                actor_id=requested_by,
                verb="snapshot.created",
                object_type="snapshot",
                object_id=snapshot.id,
                object_label=system.name,
                details={"origin": "connection", "tables": snapshot.table_count},
                at=snapshot.taken_at,
            )
            snapshot_id = snapshot.id
            # A cancellation while the Snapshot was being written discards it.
            ctx.raise_if_cancelled()
        ctx.log(f"Created Snapshot {snapshot_id}.")

    # -- internals ----------------------------------------------------------------------

    def _password(self, connection: ConnectionRecord) -> str | None:
        if connection.secret_encrypted is None:
            return None
        try:
            return self._box.decrypt(connection.secret_encrypted, context=PASSWORD_CONTEXT)
        except DecryptionError:
            raise ExtractionError(
                "The stored password does not decrypt with DAWAM_ENCRYPTION_KEY; "
                "an owner enters it again."
            ) from None

    def _load_system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        record = db.get(SourceSystemRecord, system_id)
        if record is None or record.status != "present" or record.workspace_id != workspace_id:
            raise _not_found()
        return record

    def _content(self, db: Session, record: SnapshotRecord) -> SnapshotContent:
        sid = record.id
        texts = {
            row.hash: row.text
            for row in db.execute(
                sa.select(DefinitionTextRecord.hash, DefinitionTextRecord.text).where(
                    DefinitionTextRecord.hash.in_(
                        sa.union(
                            sa.select(SnapshotTableRecord.view_definition_hash).where(
                                SnapshotTableRecord.snapshot_id == sid
                            ),
                            sa.select(SnapshotRoutineRecord.definition_hash).where(
                                SnapshotRoutineRecord.snapshot_id == sid
                            ),
                        ).scalar_subquery()
                    )
                )
            )
        }
        columns: dict[uuid.UUID, list[SnapshotColumn]] = {}
        for c in db.scalars(
            sa.select(SnapshotColumnRecord)
            .where(SnapshotColumnRecord.snapshot_id == sid)
            .order_by(SnapshotColumnRecord.src_table_id, SnapshotColumnRecord.ordinal)
        ):
            columns.setdefault(c.src_table_id, []).append(
                SnapshotColumn(
                    id=c.src_column_id,
                    name=c.name,
                    ordinal=c.ordinal,
                    data_type=c.data_type,
                    is_nullable=c.is_nullable,
                    is_pk=c.is_pk,
                    default=c.default,
                    comment=c.comment,
                )
            )
        constraints: dict[uuid.UUID, list[SnapshotConstraint]] = {}
        for k in db.scalars(
            sa.select(SnapshotConstraintRecord)
            .where(SnapshotConstraintRecord.snapshot_id == sid)
            .order_by(SnapshotConstraintRecord.name)
        ):
            constraints.setdefault(k.src_table_id, []).append(
                SnapshotConstraint(
                    name=k.name,
                    type=k.type,
                    columns=list(k.columns),
                    ref_table_id=k.ref_table_id,
                    ref_db_schema=k.ref_db_schema,
                    ref_table=k.ref_table,
                    ref_columns=list(k.ref_columns),
                )
            )
        indexes: dict[uuid.UUID, list[SnapshotIndex]] = {}
        for i in db.scalars(
            sa.select(SnapshotIndexRecord)
            .where(SnapshotIndexRecord.snapshot_id == sid)
            .order_by(SnapshotIndexRecord.name)
        ):
            indexes.setdefault(i.src_table_id, []).append(
                SnapshotIndex(name=i.name, columns=list(i.columns), is_unique=i.is_unique)
            )
        tables = [
            SnapshotTable(
                id=t.src_table_id,
                db_schema=t.db_schema,
                name=t.name,
                kind=t.kind,
                view_definition=texts.get(t.view_definition_hash or ""),
                row_estimate=t.row_estimate,
                comment=t.comment,
                columns=columns.get(t.src_table_id, []),
                constraints=constraints.get(t.src_table_id, []),
                indexes=indexes.get(t.src_table_id, []),
            )
            for t in db.scalars(
                sa.select(SnapshotTableRecord)
                .where(SnapshotTableRecord.snapshot_id == sid)
                .order_by(SnapshotTableRecord.db_schema, SnapshotTableRecord.name)
            )
        ]
        routines = [
            SnapshotRoutine(
                id=r.src_routine_id,
                db_schema=r.db_schema,
                name=r.name,
                kind=r.kind,
                signature=r.signature,
                definition=texts.get(r.definition_hash or ""),
            )
            for r in db.scalars(
                sa.select(SnapshotRoutineRecord)
                .where(SnapshotRoutineRecord.snapshot_id == sid)
                .order_by(
                    SnapshotRoutineRecord.db_schema,
                    SnapshotRoutineRecord.name,
                    SnapshotRoutineRecord.signature,
                )
            )
        ]
        db_schemas = [
            SnapshotDbSchema(id=s.src_db_schema_id, name=s.name)
            for s in db.scalars(
                sa.select(SnapshotDbSchemaRecord)
                .where(SnapshotDbSchemaRecord.snapshot_id == sid)
                .order_by(SnapshotDbSchemaRecord.name)
            )
        ]
        return SnapshotContent(
            snapshot=_summary(record), db_schemas=db_schemas, tables=tables, routines=routines
        )
