"""Metadata extraction into Snapshots: the service API (spec stories 45, 46, §6.3).

An owner or editor starts an extraction (``start_extraction``); it runs as an
``extract`` background job (``run_extraction`` is its handler) that reads the
Connection's allowed Database Schemas and stores the result as a new Snapshot, unless
nothing changed since the latest one. Any member reads Snapshots (``list`` and
``get``), archived Workspaces included, and browse and search the Source Schema
(``source_schema``, ``search``).

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
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcRoutineRecord,
    SrcTableRecord,
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
    status: str | None
    """The Source Object's state now, set only in the Source Schema (a Snapshot never changes)."""


@dataclass(frozen=True)
class SnapshotColumn:
    id: uuid.UUID
    """The Source Object (``SrcColumn``): the same in every Snapshot."""
    name: str
    status: str | None
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
    status: str | None
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
    status: str | None
    signature: str
    definition: str | None


@dataclass(frozen=True)
class SnapshotContent:
    snapshot: SnapshotSummary
    db_schemas: list[SnapshotDbSchema]
    tables: list[SnapshotTable]
    routines: list[SnapshotRoutine]


@dataclass(frozen=True)
class RemovedTable:
    """A table or view the latest Snapshot no longer has, kept visible and flagged."""

    id: uuid.UUID
    db_schema: str
    name: str
    kind: str
    status: str
    """``source_removed`` (gone from the source) or ``out_of_scope`` (outside the allowed
    Database Schemas)."""


@dataclass(frozen=True)
class RemovedColumn:
    """A column of a table in the latest Snapshot that the Snapshot no longer has."""

    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    status: str
    data_type: str | None
    """As of the latest Snapshot that had it."""


@dataclass(frozen=True)
class SourceSchema:
    """The Source System's latest Snapshot plus the Source Objects it lacks."""

    content: SnapshotContent
    removed_tables: list[RemovedTable]
    removed_columns: list[RemovedColumn]


@dataclass(frozen=True)
class SearchHit:
    kind: str
    """``db_schema``, ``table``, ``view``, ``column`` or ``routine``."""
    id: uuid.UUID
    name: str
    db_schema: str | None
    """The Database Schema holding it; ``None`` for a Database Schema itself."""
    table: str | None
    """A column's table."""
    status: str


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
                .order_by(SnapshotRecord.taken_at.desc(), SnapshotRecord.is_latest.desc())
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

    def source_schema(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSchema:
        """The latest Snapshot with its objects' states, and the tables and views it no
        longer has but that are still tracked (any member). 404 before the first Snapshot."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            record = self._latest(db, system_id)
            kept = sa.select(SnapshotTableRecord.src_table_id).where(
                SnapshotTableRecord.snapshot_id == record.id
            )
            removed = [
                RemovedTable(
                    id=table.id,
                    db_schema=schema_name,
                    name=table.name,
                    kind=table.kind,
                    status=table.status,
                )
                for table, schema_name in db.execute(
                    sa.select(SrcTableRecord, SrcDbSchemaRecord.name)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(
                        SrcDbSchemaRecord.source_system_id == system_id,
                        SrcTableRecord.status.in_(("source_removed", "out_of_scope")),
                        SrcTableRecord.id.not_in(kept),
                    )
                    .order_by(SrcDbSchemaRecord.name, SrcTableRecord.name)
                )
            ]
            kept_columns = sa.select(SnapshotColumnRecord.src_column_id).where(
                SnapshotColumnRecord.snapshot_id == record.id
            )
            removed_columns = [
                RemovedColumn(
                    id=column.id,
                    table_id=column.table_id,
                    name=column.name,
                    status=column.status,
                    data_type=column.current_definition.get("data_type"),
                )
                for column in db.scalars(
                    sa.select(SrcColumnRecord)
                    .where(
                        SrcColumnRecord.table_id.in_(kept),
                        SrcColumnRecord.status.in_(("source_removed", "out_of_scope")),
                        SrcColumnRecord.id.not_in(kept_columns),
                    )
                    .order_by(SrcColumnRecord.table_id, SrcColumnRecord.name)
                )
            ]
            return SourceSchema(
                content=self._content(db, record, with_status=True),
                removed_tables=removed,
                removed_columns=removed_columns,
            )

    def search(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, query: str, limit: int
    ) -> list[SearchHit]:
        """Database Schemas, tables, views, columns and routines of the latest Snapshot
        whose name contains ``query`` (case-insensitive; any member). Exact matches come
        first, then names starting with it, then the rest."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._load_system(db, workspace_id, system_id)
            record = self._latest(db, system_id)
            term = query.strip()
            return self._search(db, record.id, term, limit) if term else []

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

    def _latest(self, db: Session, system_id: uuid.UUID) -> SnapshotRecord:
        record = db.scalars(
            sa.select(SnapshotRecord).where(
                SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
            )
        ).first()
        if record is None:
            raise _not_found("Snapshot")
        return record

    def _search(
        self, db: Session, snapshot_id: uuid.UUID, term: str, limit: int
    ) -> list[SearchHit]:
        escape = "\\"
        escaped = term.replace(escape, escape * 2).replace("%", escape + "%")
        escaped = escaped.replace("_", escape + "_")
        contains, prefix = f"%{escaped}%", f"{escaped}%"
        schemas = (
            sa.select(
                sa.literal("db_schema").label("kind"),
                SnapshotDbSchemaRecord.src_db_schema_id.label("id"),
                SnapshotDbSchemaRecord.name.label("name"),
                sa.null().label("db_schema"),
                sa.null().label("tbl"),
                SrcDbSchemaRecord.status.label("status"),
            )
            .join(
                SrcDbSchemaRecord, SrcDbSchemaRecord.id == SnapshotDbSchemaRecord.src_db_schema_id
            )
            .where(
                SnapshotDbSchemaRecord.snapshot_id == snapshot_id,
                SnapshotDbSchemaRecord.name.ilike(contains, escape=escape),
            )
        )
        tables = (
            sa.select(
                SnapshotTableRecord.kind.label("kind"),
                SnapshotTableRecord.src_table_id.label("id"),
                SnapshotTableRecord.name.label("name"),
                SnapshotTableRecord.db_schema.label("db_schema"),
                sa.null().label("tbl"),
                SrcTableRecord.status.label("status"),
            )
            .join(SrcTableRecord, SrcTableRecord.id == SnapshotTableRecord.src_table_id)
            .where(
                SnapshotTableRecord.snapshot_id == snapshot_id,
                SnapshotTableRecord.name.ilike(contains, escape=escape),
            )
        )
        columns = (
            sa.select(
                sa.literal("column").label("kind"),
                SnapshotColumnRecord.src_column_id.label("id"),
                SnapshotColumnRecord.name.label("name"),
                SnapshotTableRecord.db_schema.label("db_schema"),
                SnapshotTableRecord.name.label("tbl"),
                SrcColumnRecord.status.label("status"),
            )
            .join(SrcColumnRecord, SrcColumnRecord.id == SnapshotColumnRecord.src_column_id)
            .join(
                SnapshotTableRecord,
                sa.and_(
                    SnapshotTableRecord.snapshot_id == SnapshotColumnRecord.snapshot_id,
                    SnapshotTableRecord.src_table_id == SnapshotColumnRecord.src_table_id,
                ),
            )
            .where(
                SnapshotColumnRecord.snapshot_id == snapshot_id,
                SnapshotColumnRecord.name.ilike(contains, escape=escape),
            )
        )
        routines = (
            sa.select(
                sa.literal("routine").label("kind"),
                SnapshotRoutineRecord.src_routine_id.label("id"),
                SnapshotRoutineRecord.name.label("name"),
                SnapshotRoutineRecord.db_schema.label("db_schema"),
                sa.null().label("tbl"),
                SrcRoutineRecord.status.label("status"),
            )
            .join(SrcRoutineRecord, SrcRoutineRecord.id == SnapshotRoutineRecord.src_routine_id)
            .where(
                SnapshotRoutineRecord.snapshot_id == snapshot_id,
                SnapshotRoutineRecord.name.ilike(contains, escape=escape),
            )
        )
        hits = sa.union_all(schemas, tables, columns, routines).subquery()
        rank = sa.case(
            (sa.func.lower(hits.c.name) == term.lower(), 0),
            (hits.c.name.ilike(prefix, escape=escape), 1),
            else_=2,
        )
        rows = db.execute(
            sa.select(hits)
            .order_by(rank, hits.c.name, hits.c.db_schema, hits.c.tbl, hits.c.kind)
            .limit(limit)
        )
        return [
            SearchHit(
                kind=r.kind,
                id=r.id,
                name=r.name,
                db_schema=r.db_schema,
                table=r.tbl,
                status=r.status,
            )
            for r in rows
        ]

    def _statuses(self, db: Session, snapshot_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """The current state of every Source Object the Snapshot has."""
        statuses: dict[uuid.UUID, str] = {}
        for source_object, snapshot_row, link in (
            (SrcDbSchemaRecord, SnapshotDbSchemaRecord, SnapshotDbSchemaRecord.src_db_schema_id),
            (SrcTableRecord, SnapshotTableRecord, SnapshotTableRecord.src_table_id),
            (SrcColumnRecord, SnapshotColumnRecord, SnapshotColumnRecord.src_column_id),
            (SrcRoutineRecord, SnapshotRoutineRecord, SnapshotRoutineRecord.src_routine_id),
        ):
            statuses.update(
                db.execute(
                    sa.select(source_object.id, source_object.status)
                    .join(snapshot_row, link == source_object.id)
                    .where(snapshot_row.snapshot_id == snapshot_id)
                ).all()
            )
        return statuses

    def _content(
        self, db: Session, record: SnapshotRecord, *, with_status: bool = False
    ) -> SnapshotContent:
        sid = record.id
        statuses = self._statuses(db, sid) if with_status else {}
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
                    status=statuses.get(c.src_column_id),
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
                status=statuses.get(t.src_table_id),
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
                status=statuses.get(r.src_routine_id),
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
            SnapshotDbSchema(
                id=s.src_db_schema_id, name=s.name, status=statuses.get(s.src_db_schema_id)
            )
            for s in db.scalars(
                sa.select(SnapshotDbSchemaRecord)
                .where(SnapshotDbSchemaRecord.snapshot_id == sid)
                .order_by(SnapshotDbSchemaRecord.name)
            )
        ]
        return SnapshotContent(
            snapshot=_summary(record), db_schemas=db_schemas, tables=tables, routines=routines
        )
