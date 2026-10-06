"""Profiling of Source Tables through a live Connection (spec §6.5, stories 55, 56, 57).

An owner or editor starts a ``profile`` job for chosen tables (``start_profiling``;
``run_profiling`` is its handler). It reads a capped sample of each table (``row_cap``,
default 100 000 rows) under a query timeout, and keeps the latest ``ColumnProfile`` of
each column. Top-N values are off unless an owner switched them on for the table
(``set_top_n``), and a Protected Column (``internal.pii.is_protected``, the one policy)
never gets min/max or top-N: not when it is profiled, and not when it is read, so a
column that becomes protected after profiling is redacted at once. Any member reads
profiles. A Source System with no Connection (one built from a Schema Import) cannot be
profiled.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import ConnectionParams, Connector, ConnectorError, connector_for
from .internal.pii import protected_column_ids
from .tables import (
    ColumnProfileRecord,
    ConnectionRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

PROFILE_JOB = "profile"
DEFAULT_ROW_CAP = 100_000
MAX_ROW_CAP = 10_000_000
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 600
MAX_TABLES = 200


@dataclass(frozen=True)
class TopValue:
    value: str
    count: int


@dataclass(frozen=True)
class ColumnProfile:
    row_count: int
    """Rows in the sample."""
    row_cap: int
    sampled: bool
    """The sample hit its cap, so the table may have more rows than ``row_count``."""
    null_pct: float
    distinct_count: int | None
    min: str | None
    max: str | None
    avg_len: float | None
    max_len: int | None
    top_values: list[TopValue] | None
    patterns: list[str]
    profiled_at: datetime


@dataclass(frozen=True)
class ColumnProfileView:
    column_id: uuid.UUID
    name: str
    data_type: str | None
    is_protected: bool
    profile: ColumnProfile | None
    """``None`` until the column is profiled. For a Protected Column ``min``, ``max`` and
    ``top_values`` are always ``None``."""


@dataclass(frozen=True)
class TableProfile:
    table_id: uuid.UUID
    db_schema: str
    name: str
    top_n_enabled: bool
    row_count: int | None
    """The largest sample row count of its columns; ``None`` before profiling."""
    profiled_at: datetime | None
    columns: list[ColumnProfileView]


class ProfilingError(Exception):
    """Why a profiling job failed; its message is safe to show."""


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class ProfilingService:
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

    def start_profiling(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_ids: list[uuid.UUID],
        *,
        row_cap: int = DEFAULT_ROW_CAP,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Job:
        """Queue a ``profile`` job for the tables (owners and editors). 409
        ``profiling_unavailable`` if the Source System has no live Connection (a Schema
        Import has none); 404 for a table that is not a present table of the system."""
        self._workspaces.authorize(user, Action.RUN_PROFILING, workspace_id)
        if not 1 <= row_cap <= MAX_ROW_CAP:
            raise ApiError(422, "invalid_row_cap", f"The row cap must be 1 to {MAX_ROW_CAP}.")
        if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ApiError(
                422,
                "invalid_timeout",
                f"The timeout must be 1 to {MAX_TIMEOUT_SECONDS} seconds.",
            )
        wanted = list(dict.fromkeys(table_ids))
        if not 1 <= len(wanted) <= MAX_TABLES:
            raise ApiError(422, "invalid_tables", f"Choose 1 to {MAX_TABLES} tables.")
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            # Invariant: a Source System with no Connection was built from a Schema Import.
            has_connection = db.scalar(
                sa.select(ConnectionRecord.id).where(ConnectionRecord.source_system_id == system_id)
            )
            if has_connection is None:
                raise ApiError(
                    409,
                    "profiling_unavailable",
                    "Profiling needs a live Connection to the source database. This Source "
                    "System has none (for example, it was built from a Schema Import): an "
                    "owner adds a Connection first.",
                )
            found = set(
                db.scalars(
                    sa.select(SrcTableRecord.id)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(
                        SrcTableRecord.id.in_(wanted),
                        SrcDbSchemaRecord.source_system_id == system_id,
                        SrcTableRecord.status == "present",
                    )
                )
            )
            if found != set(wanted):
                raise _not_found("Table")
            return self._jobs.submit(
                workspace_id,
                PROFILE_JOB,
                {
                    "source_system_id": str(system_id),
                    "table_ids": [str(t) for t in wanted],
                    "row_cap": row_cap,
                    "timeout_seconds": timeout_seconds,
                    "requested_by": str(user.id),
                },
                title=f"Profile {len(wanted)} table(s): {system.name}",
                created_by=user.id,
                db=db,
            )

    def table_profile(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, table_id: uuid.UUID
    ) -> TableProfile:
        """A table's profile with each of its columns' (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            return self._table_profile(db, system_id, table_id)

    def column_profile(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_id: uuid.UUID,
        column_id: uuid.UUID,
    ) -> ColumnProfileView:
        """One column's profile (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            table = self._table_profile(db, system_id, table_id)
            for column in table.columns:
                if column.column_id == column_id:
                    return column
            raise _not_found("Column")

    def set_top_n(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_id: uuid.UUID,
        enabled: bool,
    ) -> TableProfile:
        """Switch top-N value capture for a table (owners only; audited). Switching it off
        deletes the top-N values already stored for the table."""
        self._workspaces.authorize(user, Action.ENABLE_TOP_N, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            table = self._load_table(db, system_id, table_id, lock=True)
            if table.top_n_enabled != enabled:
                now = self._clock()
                table.top_n_enabled = enabled
                table.version += 1
                if not enabled:
                    db.execute(
                        sa.update(ColumnProfileRecord)
                        .where(
                            ColumnProfileRecord.src_column_id.in_(
                                sa.select(SrcColumnRecord.id).where(
                                    SrcColumnRecord.table_id == table_id
                                )
                            )
                        )
                        .values(top_values=None)
                    )
                record_audit(
                    db,
                    workspace_id=system.workspace_id,
                    actor_id=user.id,
                    entity_type="source_table",
                    entity_id=table.id,
                    old={"top_n_enabled": not enabled},
                    new={"top_n_enabled": enabled},
                    at=now,
                )
            db.flush()
            return self._table_profile(db, system_id, table_id)

    # -- the job ------------------------------------------------------------------------

    def run_profiling(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``profile`` job handler: profile each chosen table's columns."""
        system_id = uuid.UUID(params["source_system_id"])
        table_ids = [uuid.UUID(t) for t in params["table_ids"]]
        row_cap = int(params.get("row_cap", DEFAULT_ROW_CAP))
        timeout = int(params.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            if system is None or system.status != "present" or connection is None:
                raise ProfilingError("The Source System or its Connection no longer exists.")
            engine_name = connection.engine
            source = ConnectionParams(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                username=connection.username,
                password=self._password(connection),
                allowed_schemas=tuple(connection.allowed_schemas),
                options={**connection.options, "statement_timeout_seconds": timeout},
            )
        connector = self._connectors(engine_name, source)
        failed = done = 0
        for index, table_id in enumerate(table_ids):
            ctx.raise_if_cancelled()
            ctx.progress(int(100 * index / len(table_ids)))
            plan = self._plan(system_id, table_id)
            if plan is None:
                ctx.log(f"Skipped a table that no longer exists ({table_id}).")
                continue
            schema, name, top_n, columns = plan
            ctx.log(f"Profiling {schema}.{name} ({len(columns)} columns, sample {row_cap} rows).")
            for column_id, column, data_type, protected in columns:
                ctx.raise_if_cancelled()
                try:
                    stats = connector.profile_column(
                        schema,
                        name,
                        column,
                        data_type,
                        row_cap=row_cap,
                        top_n=top_n and not protected,
                        with_min_max=not protected,
                    )
                except ConnectorError as exc:
                    failed += 1
                    ctx.log(f"  {column}: {exc.message}")
                    continue
                self._store(column_id, stats, row_cap, ctx.job_id)
                done += 1
        if done == 0 and failed:
            raise ProfilingError("No column could be profiled; see the log.")
        ctx.log(f"Profiled {done} columns" + (f"; {failed} failed." if failed else "."))

    # -- internals ----------------------------------------------------------------------

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        record = db.get(SourceSystemRecord, system_id)
        if record is None or record.status != "present" or record.workspace_id != workspace_id:
            raise _not_found("Source System")
        return record

    def _load_table(
        self, db: Session, system_id: uuid.UUID, table_id: uuid.UUID, *, lock: bool = False
    ) -> SrcTableRecord:
        query = (
            sa.select(SrcTableRecord)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(SrcTableRecord.id == table_id, SrcDbSchemaRecord.source_system_id == system_id)
        )
        table = db.scalars(query.with_for_update(of=SrcTableRecord) if lock else query).first()
        if table is None:
            raise _not_found("Table")
        return table

    def _password(self, connection: ConnectionRecord) -> str | None:
        if connection.secret_encrypted is None:
            return None
        try:
            return self._box.decrypt(connection.secret_encrypted, context=PASSWORD_CONTEXT)
        except DecryptionError:
            raise ProfilingError(
                "The stored password does not decrypt with DAWAM_ENCRYPTION_KEY; "
                "an owner enters it again."
            ) from None

    def _plan(
        self, system_id: uuid.UUID, table_id: uuid.UUID
    ) -> tuple[str, str, bool, list[tuple[uuid.UUID, str, str, bool]]] | None:
        """What to profile in a table, read when its turn comes so the Protected Column
        policy and the top-N switch are as current as they can be."""
        with Session(self._engine) as db:
            row = db.execute(
                sa.select(SrcTableRecord, SrcDbSchemaRecord.name)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(
                    SrcTableRecord.id == table_id,
                    SrcDbSchemaRecord.source_system_id == system_id,
                    SrcTableRecord.status == "present",
                )
            ).first()
            if row is None:
                return None
            table, schema = row
            columns = list(
                db.scalars(
                    sa.select(SrcColumnRecord)
                    .where(
                        SrcColumnRecord.table_id == table_id, SrcColumnRecord.status == "present"
                    )
                    .order_by(SrcColumnRecord.name)
                )
            )
            protected = protected_column_ids(db, [c.id for c in columns])
            return (
                schema,
                table.name,
                table.top_n_enabled,
                [
                    (
                        c.id,
                        c.name,
                        str(c.current_definition.get("data_type", "")),
                        c.id in protected,
                    )
                    for c in columns
                ],
            )

    def _store(self, column_id: uuid.UUID, stats: Any, row_cap: int, job_id: uuid.UUID) -> None:
        with Session(self._engine) as db, db.begin():
            # Re-read the policy under the write: a column flagged while the source was
            # being read must not keep a value.
            column = db.scalars(
                sa.select(SrcColumnRecord).where(SrcColumnRecord.id == column_id)
            ).first()
            if column is None:
                return
            table = db.get(SrcTableRecord, column.table_id)
            protected = column_id in protected_column_ids(db, [column_id])
            keep_top = bool(table and table.top_n_enabled) and not protected
            top = (
                [{"value": v, "count": c} for v, c in stats.top_values]
                if keep_top and stats.top_values is not None
                else None
            )
            values = {
                "job_id": job_id,
                "row_count": stats.row_count,
                "row_cap": row_cap,
                "null_pct": (100.0 * stats.null_count / stats.row_count)
                if stats.row_count
                else 0.0,
                "distinct_count": stats.distinct_count,
                "min": None if protected else stats.minimum,
                "max": None if protected else stats.maximum,
                "avg_len": stats.avg_length,
                "max_len": stats.max_length,
                "top_values": top,
                "patterns": list(stats.patterns),
                "profiled_at": self._clock(),
            }
            existing = db.scalars(
                sa.select(ColumnProfileRecord).where(ColumnProfileRecord.src_column_id == column_id)
            ).first()
            if existing is None:
                db.add(ColumnProfileRecord(src_column_id=column_id, **values))
            else:
                for field, value in values.items():
                    setattr(existing, field, value)

    def _table_profile(
        self, db: Session, system_id: uuid.UUID, table_id: uuid.UUID
    ) -> TableProfile:
        table = self._load_table(db, system_id, table_id)
        schema = db.scalar(
            sa.select(SrcDbSchemaRecord.name).where(SrcDbSchemaRecord.id == table.db_schema_id)
        )
        columns = list(
            db.scalars(
                sa.select(SrcColumnRecord)
                .where(SrcColumnRecord.table_id == table_id, SrcColumnRecord.status == "present")
                .order_by(SrcColumnRecord.name)
            )
        )
        protected = protected_column_ids(db, [c.id for c in columns])
        records = {
            r.src_column_id: r
            for r in db.scalars(
                sa.select(ColumnProfileRecord).where(
                    ColumnProfileRecord.src_column_id.in_([c.id for c in columns])
                )
            )
        }
        views = [
            ColumnProfileView(
                column_id=c.id,
                name=c.name,
                data_type=c.current_definition.get("data_type"),
                is_protected=c.id in protected,
                profile=_profile(
                    records.get(c.id), protected=c.id in protected, top_n=table.top_n_enabled
                ),
            )
            for c in columns
        ]
        profiled = [v.profile for v in views if v.profile is not None]
        return TableProfile(
            table_id=table.id,
            db_schema=schema or "",
            name=table.name,
            top_n_enabled=table.top_n_enabled,
            row_count=max((p.row_count for p in profiled), default=None),
            profiled_at=max((p.profiled_at for p in profiled), default=None),
            columns=views,
        )


def _profile(
    record: ColumnProfileRecord | None, *, protected: bool, top_n: bool
) -> ColumnProfile | None:
    if record is None:
        return None
    show_top = top_n and not protected and record.top_values is not None
    return ColumnProfile(
        row_count=record.row_count,
        row_cap=record.row_cap,
        sampled=record.row_count >= record.row_cap,
        null_pct=record.null_pct,
        distinct_count=record.distinct_count,
        min=None if protected else record.min,
        max=None if protected else record.max,
        avg_len=record.avg_len,
        max_len=record.max_len,
        top_values=(
            [TopValue(value=t["value"], count=t["count"]) for t in record.top_values or []]
            if show_top
            else None
        ),
        patterns=list(record.patterns),
        profiled_at=record.profiled_at,
    )
