"""Relationship inference and review (spec §6.6, stories 58, 59).

An owner or editor starts an ``infer_relationships`` job for a Source System
(``start_inference``; ``run_inference`` is its handler). It scores candidate column pairs
from the latest Snapshot (``internal.inference``) and keeps those at or above the
threshold (default 0.6) as ``suggested`` relationships with their evidence. Value overlap
joins the score only when the system has a live Connection and both columns are profiled:
the job samples the two tables, compares the values in memory and keeps the ratio alone.
Any member lists the relationships; owners and editors ``accept`` or ``reject`` one, each
decision audited and recorded in the activity feed. A decision is never undone by a later
run: the run only refreshes a pair's confidence and evidence, and drops ``suggested``
pairs that no longer qualify.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import ConnectionParams, Connector, ConnectorError, connector_for
from .internal.inference import (
    DEFAULT_THRESHOLD,
    Candidate,
    Col,
    find_candidates,
    routine_joins,
)
from .internal.pii import protected_column_ids
from .tables import (
    RELATIONSHIP_STATUSES,
    ColumnProfileRecord,
    ConnectionRecord,
    DefinitionTextRecord,
    RelationshipRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotIndexRecord,
    SnapshotRecord,
    SnapshotRoutineRecord,
    SnapshotTableRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

INFER_JOB = "infer_relationships"
DEFAULT_SAMPLE_SIZE = 5_000
MAX_SAMPLE_SIZE = 100_000
_DIALECTS = {"postgresql": "postgres", "sqlserver": "tsql", "mysql": "mysql", "oracle": "oracle"}


class InferenceError(Exception):
    """Why an inference job failed; its message is safe to show."""


@dataclass(frozen=True)
class ColumnRef:
    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str


@dataclass(frozen=True)
class Relationship:
    id: uuid.UUID
    from_column: ColumnRef
    to_column: ColumnRef
    origin: str
    confidence: float
    evidence: dict[str, Any]
    status: str
    """``suggested``, ``accepted`` or ``rejected``."""
    version: int
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class RelationshipService:
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

    def start_inference(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
    ) -> Job:
        """Queue an ``infer_relationships`` job (owners and editors). 409 ``no_snapshot``
        before the first Snapshot; 422 for a threshold outside (0, 1] or a bad sample size."""
        self._workspaces.authorize(user, Action.RUN_PROFILING, workspace_id)
        if not 0 < threshold <= 1:
            raise ApiError(422, "invalid_threshold", "The threshold is above 0 and at most 1.")
        if not 1 <= sample_size <= MAX_SAMPLE_SIZE:
            raise ApiError(
                422, "invalid_sample_size", f"The sample size is 1 to {MAX_SAMPLE_SIZE} rows."
            )
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            if self._latest_snapshot(db, system_id) is None:
                raise ApiError(
                    409,
                    "no_snapshot",
                    "Relationships are inferred from a Snapshot: extract or import the "
                    "schema first.",
                )
            return self._jobs.submit(
                workspace_id,
                INFER_JOB,
                {
                    "source_system_id": str(system_id),
                    "threshold": threshold,
                    "sample_size": sample_size,
                },
                title=f"Infer relationships: {system.name}",
                created_by=user.id,
                db=db,
            )

    def list_relationships(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        status: str | None = None,
        min_confidence: float = DEFAULT_THRESHOLD,
    ) -> list[Relationship]:
        """The Source System's relationships at or above ``min_confidence`` (default 0.6),
        most confident first; ``status`` narrows to one state (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        if status is not None and status not in RELATIONSHIP_STATUSES:
            raise ApiError(
                422,
                "invalid_status",
                f"The status must be one of {', '.join(RELATIONSHIP_STATUSES)}.",
            )
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            query = self._select().where(
                SrcDbSchemaRecord.source_system_id == system_id,
                RelationshipRecord.confidence >= min_confidence - 1e-9,
            )
            if status is not None:
                query = query.where(RelationshipRecord.status == status)
            rows = db.execute(
                query.order_by(RelationshipRecord.confidence.desc(), RelationshipRecord.detected_at)
            ).all()
            return [self._view(*row) for row in rows]

    def accept(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, relationship_id: uuid.UUID
    ) -> Relationship:
        """Accept a relationship: the Source Schema now treats it as real."""
        return self._decide(user, workspace_id, system_id, relationship_id, "accepted")

    def reject(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, relationship_id: uuid.UUID
    ) -> Relationship:
        """Reject a relationship; later runs do not propose it again."""
        return self._decide(user, workspace_id, system_id, relationship_id, "rejected")

    # -- the job ------------------------------------------------------------------------

    def run_inference(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``infer_relationships`` job handler."""
        system_id = uuid.UUID(params["source_system_id"])
        threshold = float(params.get("threshold", DEFAULT_THRESHOLD))
        sample_size = int(params.get("sample_size", DEFAULT_SAMPLE_SIZE))
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present":
                raise InferenceError("The Source System no longer exists.")
            snapshot_id = self._latest_snapshot(db, system_id)
            if snapshot_id is None:
                raise InferenceError("The Source System has no Snapshot.")
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            dialect = _DIALECTS.get(connection.engine) if connection else None
            columns, profiled = self._columns(db, system_id, snapshot_id)
            declared = self._declared(db, snapshot_id, columns)
            joins = self._joins(db, snapshot_id, columns, dialect)
            params_for_source = self._source_params(connection) if connection else None
            engine_name = connection.engine if connection else None
        ctx.log(
            f"{len(columns)} columns in the latest Snapshot, {len(declared)} declared "
            f"foreign key pairs, {len(joins)} joined column pairs in views and routines."
        )
        ctx.raise_if_cancelled()
        candidates = find_candidates(columns, joins, declared, threshold=threshold)
        ctx.log(f"{len(candidates)} candidates before value overlap.")
        if params_for_source is not None and engine_name is not None:
            measured = self._measure_overlap(
                candidates,
                profiled,
                self._connectors(engine_name, params_for_source),
                sample_size,
                ctx,
            )
            ctx.log(f"Value overlap measured for {measured} candidates.")
        else:
            ctx.log("No live Connection: value overlap skipped.")
        kept = [c for c in candidates if c.confidence + 1e-9 >= threshold]
        created, updated, dropped = self._record(system_id, kept, threshold)
        ctx.log(
            f"Done: {len(kept)} relationships at or above {threshold:g} "
            f"({created} new, {updated} refreshed, {dropped} withdrawn)."
        )

    # -- loading the model --------------------------------------------------------------

    def _columns(
        self, db: Session, system_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> tuple[list[Col], set[uuid.UUID]]:
        """The present columns of base tables in the Snapshot, with how their uniqueness is
        known; and the ids of those that have a profile."""
        rows = db.execute(
            sa.select(
                SrcColumnRecord.id,
                SrcTableRecord.id,
                SrcDbSchemaRecord.name,
                SrcTableRecord.name,
                SrcColumnRecord.name,
                SnapshotColumnRecord.data_type,
            )
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .join(
                SnapshotColumnRecord,
                sa.and_(
                    SnapshotColumnRecord.src_column_id == SrcColumnRecord.id,
                    SnapshotColumnRecord.snapshot_id == snapshot_id,
                ),
            )
            .where(
                SrcDbSchemaRecord.source_system_id == system_id,
                SrcColumnRecord.status == "present",
                SrcTableRecord.status == "present",
                SrcTableRecord.kind == "table",
            )
            .order_by(SrcDbSchemaRecord.name, SrcTableRecord.name, SnapshotColumnRecord.ordinal)
        ).all()
        unique_columns: dict[tuple[uuid.UUID, str], str] = {}
        for table_id, columns, kind in db.execute(
            sa.select(
                SnapshotConstraintRecord.src_table_id,
                SnapshotConstraintRecord.columns,
                SnapshotConstraintRecord.type,
            ).where(
                SnapshotConstraintRecord.snapshot_id == snapshot_id,
                SnapshotConstraintRecord.type.in_(("pk", "unique")),
            )
        ):
            if len(columns) == 1:
                unique_columns[table_id, columns[0]] = (
                    "primary key" if kind == "pk" else "unique index"
                )
        for table_id, columns in db.execute(
            sa.select(SnapshotIndexRecord.src_table_id, SnapshotIndexRecord.columns).where(
                SnapshotIndexRecord.snapshot_id == snapshot_id, SnapshotIndexRecord.is_unique
            )
        ):
            if len(columns) == 1:
                unique_columns.setdefault((table_id, columns[0]), "unique index")
        ids = [row[0] for row in rows]
        profiles = {
            column_id: (row_count, null_pct, distinct)
            for column_id, row_count, null_pct, distinct in db.execute(
                sa.select(
                    ColumnProfileRecord.src_column_id,
                    ColumnProfileRecord.row_count,
                    ColumnProfileRecord.null_pct,
                    ColumnProfileRecord.distinct_count,
                ).where(ColumnProfileRecord.src_column_id.in_(ids))
            )
        }
        columns_out: list[Col] = []
        for column_id, table_id, schema, table, name, data_type in rows:
            via = unique_columns.get((table_id, name))
            profile = profiles.get(column_id)
            if via is None and profile is not None:
                row_count, null_pct, distinct = profile
                if row_count > 0 and null_pct == 0 and distinct == row_count:
                    via = "profiled: distinct = rows"
            columns_out.append(Col(column_id, table_id, schema, table, name, data_type, via))
        # A Protected Column never takes part in value overlap (the one policy, as profiling).
        return columns_out, set(profiles) - protected_column_ids(db, ids)

    def _declared(
        self, db: Session, snapshot_id: uuid.UUID, columns: list[Col]
    ) -> set[frozenset[uuid.UUID]]:
        """The column pairs of declared foreign keys (never proposed again)."""
        by_name = {(c.table_id, c.name): c.id for c in columns}
        pairs: set[frozenset[uuid.UUID]] = set()
        for table_id, ref_table_id, names, ref_names in db.execute(
            sa.select(
                SnapshotConstraintRecord.src_table_id,
                SnapshotConstraintRecord.ref_table_id,
                SnapshotConstraintRecord.columns,
                SnapshotConstraintRecord.ref_columns,
            ).where(
                SnapshotConstraintRecord.snapshot_id == snapshot_id,
                SnapshotConstraintRecord.type == "fk",
                SnapshotConstraintRecord.ref_table_id.is_not(None),
            )
        ):
            for name, ref_name in zip(names, ref_names, strict=False):
                a, b = by_name.get((table_id, name)), by_name.get((ref_table_id, ref_name))
                if a is not None and b is not None:
                    pairs.add(frozenset((a, b)))
        return pairs

    def _joins(
        self, db: Session, snapshot_id: uuid.UUID, columns: list[Col], dialect: str | None
    ) -> dict[tuple[uuid.UUID, uuid.UUID], list[str]]:
        """JOIN conditions in the Snapshot's view and routine text, as column-id pairs."""
        texts: list[tuple[str, str]] = []
        for schema, name, text in db.execute(
            sa.select(
                SnapshotTableRecord.db_schema, SnapshotTableRecord.name, DefinitionTextRecord.text
            )
            .join(
                DefinitionTextRecord,
                DefinitionTextRecord.hash == SnapshotTableRecord.view_definition_hash,
            )
            .where(SnapshotTableRecord.snapshot_id == snapshot_id)
        ):
            texts.append((f"view {schema}.{name}", text))
        for schema, name, kind, text in db.execute(
            sa.select(
                SnapshotRoutineRecord.db_schema,
                SnapshotRoutineRecord.name,
                SnapshotRoutineRecord.kind,
                DefinitionTextRecord.text,
            )
            .join(
                DefinitionTextRecord,
                DefinitionTextRecord.hash == SnapshotRoutineRecord.definition_hash,
            )
            .where(SnapshotRoutineRecord.snapshot_id == snapshot_id)
        ):
            texts.append((f"{kind} {schema}.{name}", text))
        qualified = {(c.schema.lower(), c.table.lower(), c.name.lower()): c for c in columns}
        loose: dict[tuple[str, str], list[Col]] = {}
        for c in columns:
            loose.setdefault((c.table.lower(), c.name.lower()), []).append(c)

        def resolve(side: Any) -> Col | None:
            if side.schema is not None:
                return qualified.get((side.schema.lower(), side.table.lower(), side.column.lower()))
            matches = loose.get((side.table.lower(), side.column.lower()), [])
            return matches[0] if len(matches) == 1 else None

        found: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = {}
        for label, text in texts:
            for left, right in routine_joins(text, dialect):
                a, b = resolve(left), resolve(right)
                if a is None or b is None or a.table_id == b.table_id:
                    continue
                key = (a.id, b.id) if str(a.id) < str(b.id) else (b.id, a.id)
                found.setdefault(key, set()).add(label)
        return {key: sorted(labels) for key, labels in found.items()}

    # -- value overlap ------------------------------------------------------------------

    def _measure_overlap(
        self,
        candidates: list[Candidate],
        profiled: set[uuid.UUID],
        connector: Connector,
        sample_size: int,
        ctx: JobContext,
    ) -> int:
        """Set ``overlap`` on each candidate whose two columns are profiled: the share of
        the source column's distinct sampled values found in the target column. Measured
        only when the sample holds the whole target table; protected columns are not in
        ``profiled``.
        Values live only in this call's locals."""
        eligible = [c for c in candidates if c.from_col.id in profiled and c.to_col.id in profiled]
        wanted: dict[uuid.UUID, dict[str, Col]] = {}
        tables: dict[uuid.UUID, Col] = {}
        for c in eligible:
            for col in (c.from_col, c.to_col):
                wanted.setdefault(col.table_id, {})[col.name] = col
                tables[col.table_id] = col
        values: dict[uuid.UUID, set[Any]] = {}
        rows_sampled: dict[uuid.UUID, int] = {}
        complete: dict[uuid.UUID, bool] = {}
        for table_id, named in wanted.items():
            ctx.raise_if_cancelled()
            anchor = tables[table_id]
            try:
                sample = connector.sample(anchor.schema, anchor.table, limit=sample_size + 1)
            except ConnectorError as exc:
                ctx.log(f"{anchor.schema}.{anchor.table}: no sample ({exc.message}).")
                continue
            complete[table_id] = len(sample.rows) <= sample_size
            rows = sample.rows[:sample_size]
            positions = {name: i for i, name in enumerate(sample.columns)}
            for name, col in named.items():
                position = positions.get(name)
                if position is None:
                    continue
                values[col.id] = {
                    _normalised(row[position]) for row in rows if row[position] is not None
                }
                rows_sampled[col.id] = len(rows)
        measured = 0
        for c in eligible:
            source, target = values.get(c.from_col.id), values.get(c.to_col.id)
            # A sample of a larger target would miss true references: leave it unmeasured.
            if not source or target is None or not complete.get(c.to_col.table_id, False):
                continue
            c.overlap = len(source & target) / len(source)
            c.overlap_sample = (len(source), rows_sampled[c.to_col.id])
            measured += 1
        return measured

    # -- writing ------------------------------------------------------------------------

    def _record(
        self, system_id: uuid.UUID, kept: list[Candidate], threshold: float
    ) -> tuple[int, int, int]:
        now = self._clock()
        created = updated = 0
        with Session(self._engine) as db, db.begin():
            existing = {
                (r.from_column_id, r.to_column_id): r
                for r in db.scalars(
                    sa.select(RelationshipRecord)
                    .join(SrcColumnRecord, SrcColumnRecord.id == RelationshipRecord.from_column_id)
                    .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(SrcDbSchemaRecord.source_system_id == system_id)
                    .with_for_update(of=RelationshipRecord)
                )
            }
            seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
            for c in kept:
                key = (c.from_col.id, c.to_col.id)
                seen.add(key)
                record = existing.get(key)
                reverse = existing.get((c.to_col.id, c.from_col.id))
                if record is None and reverse is not None and reverse.status == "rejected":
                    seen.add(key)
                    continue  # a rejected x -> y also suppresses y -> x
                if record is None:
                    db.add(
                        RelationshipRecord(
                            id=uuid.uuid4(),
                            from_column_id=c.from_col.id,
                            to_column_id=c.to_col.id,
                            origin=c.origin,
                            confidence=c.confidence,
                            evidence=c.evidence(threshold),
                            status="suggested",
                            version=1,
                            detected_at=now,
                        )
                    )
                    created += 1
                else:
                    # A decision is never undone: only the score and its evidence move.
                    record.confidence = c.confidence
                    record.evidence = c.evidence(threshold)
                    record.origin = c.origin
                    updated += 1
            stale = [
                r.id
                for key, r in existing.items()
                if key not in seen
                and r.status == "suggested"
                and r.origin in ("inferred", "routine")
            ]
            if stale:
                db.execute(sa.delete(RelationshipRecord).where(RelationshipRecord.id.in_(stale)))
        return created, updated, len(stale)

    # -- internals ----------------------------------------------------------------------

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        system = db.get(SourceSystemRecord, system_id)
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    def _latest_snapshot(self, db: Session, system_id: uuid.UUID) -> uuid.UUID | None:
        return db.scalar(
            sa.select(SnapshotRecord.id).where(
                SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
            )
        )

    def _source_params(self, connection: ConnectionRecord) -> ConnectionParams:
        password = None
        if connection.secret_encrypted is not None:
            try:
                password = self._box.decrypt(connection.secret_encrypted, context=PASSWORD_CONTEXT)
            except DecryptionError:
                raise InferenceError(
                    "The stored password does not decrypt with DAWAM_ENCRYPTION_KEY; "
                    "an owner enters it again."
                ) from None
        return ConnectionParams(
            host=connection.host,
            port=connection.port,
            database=connection.database,
            username=connection.username,
            password=password,
            allowed_schemas=tuple(connection.allowed_schemas),
            options=dict(connection.options),
        )

    def _select(self) -> sa.Select:
        """Relationships with both columns, their tables and Database Schemas; the system
        filter is on the ``from`` side (``SrcDbSchemaRecord``)."""
        to_column = aliased(SrcColumnRecord)
        to_table = aliased(SrcTableRecord)
        to_schema = aliased(SrcDbSchemaRecord)
        return (
            sa.select(
                RelationshipRecord,
                SrcColumnRecord,
                SrcTableRecord,
                SrcDbSchemaRecord.name,
                to_column,
                to_table,
                to_schema.name,
            )
            .join(SrcColumnRecord, SrcColumnRecord.id == RelationshipRecord.from_column_id)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .join(to_column, to_column.id == RelationshipRecord.to_column_id)
            .join(to_table, to_table.id == to_column.table_id)
            .join(to_schema, to_schema.id == to_table.db_schema_id)
        )

    def _view(
        self,
        record: RelationshipRecord,
        from_column: SrcColumnRecord,
        from_table: SrcTableRecord,
        from_schema: str,
        to_column: SrcColumnRecord,
        to_table: SrcTableRecord,
        to_schema: str,
    ) -> Relationship:
        return Relationship(
            id=record.id,
            from_column=ColumnRef(
                from_column.id, from_table.id, from_schema, from_table.name, from_column.name
            ),
            to_column=ColumnRef(
                to_column.id, to_table.id, to_schema, to_table.name, to_column.name
            ),
            origin=record.origin,
            confidence=record.confidence,
            evidence=record.evidence,
            status=record.status,
            version=record.version,
            detected_at=record.detected_at,
            decided_by=record.decided_by,
            decided_at=record.decided_at,
        )

    def _decide(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        relationship_id: uuid.UUID,
        decision: str,
    ) -> Relationship:
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            row = db.execute(
                self._select()
                .where(
                    RelationshipRecord.id == relationship_id,
                    SrcDbSchemaRecord.source_system_id == system_id,
                )
                .with_for_update(of=RelationshipRecord)
            ).first()
            if row is None:
                raise _not_found("Relationship")
            record = row[0]
            old_status = record.status
            if old_status != decision:
                now = self._clock()
                record.status = decision
                record.version += 1
                record.decided_by = user.id
                record.decided_at = now
                from_column, from_table, to_column, to_table = row[1], row[2], row[4], row[5]
                record_audit(
                    db,
                    workspace_id=system.workspace_id,
                    actor_id=user.id,
                    entity_type="relationship",
                    entity_id=record.id,
                    old={"status": old_status},
                    new={"status": decision},
                    at=now,
                )
                record_activity(
                    db,
                    workspace_id=system.workspace_id,
                    actor_id=user.id,
                    verb=f"relationship.{decision}",
                    object_type="relationship",
                    object_id=record.id,
                    object_label=(
                        f"{from_table.name}.{from_column.name} -> {to_table.name}.{to_column.name}"
                    ),
                    details={
                        "source_system_id": str(system.id),
                        "origin": record.origin,
                        "confidence": record.confidence,
                    },
                    at=now,
                )
            db.flush()
            return self._view(*row)


def _normalised(value: Any) -> Any:
    """Make values comparable across driver types (``Decimal('1.0')`` and ``1``)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return value
