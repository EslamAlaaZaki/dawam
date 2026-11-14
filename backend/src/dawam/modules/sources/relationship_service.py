"""Relationship inference and review (spec §6.6, stories 58, 59, #608).

An owner or editor starts an ``infer_relationships`` job for a Source System
(``start_inference``; ``run_inference`` is its handler). In that one job:

1. **Guess.** The rules (names and JOINs in view and routine text, ``internal.inference``)
   and the AI (``relationship_ai``: metadata only, as the Workspace's AI privacy allows)
   propose pairs side by side. If the AI cannot guess, the job logs why and goes on.
2. **Union.** Pairs on the same two columns, either way round, are one pair that records
   what found it (``rules``, ``ai`` or ``both``). Declared foreign keys, rejected and
   manual pairs, and pairs whose types cannot join are dropped.
3. **Count.** With a live Connection, each pair is counted on the source, one read-only
   query at a time (counts only, ``COUNT_TIMEOUT_SECONDS`` statement timeout, which
   cancels the query on the source), most promising first; the counts set the confidence.
   A count that times out lowers the guess; one that fails keeps it, with the reason.
4. **Save.** Pairs at or above the threshold (default 0.6) are kept as ``suggested``
   relationships with their evidence: never a value.

Any member lists the relationships; owners and editors ``accept`` or ``reject`` one, each
decision audited and recorded in the activity feed. A decision is never undone by a later
run: the run only refreshes a pair's confidence and evidence, and drops ``suggested``
pairs that no longer qualify.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.llm import RoleService, WorkspaceAiService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import ConnectionParams, Connector, ConnectorError, connector_for
from .internal.inference import (
    COUNT_OUTPUTS,
    DEFAULT_THRESHOLD,
    AiGuess,
    Candidate,
    Col,
    Counts,
    count_order,
    count_sql,
    find_candidates,
    reverse_wins,
    routine_joins,
    union,
)
from .pii_ai_service import PiiDocumentSearch
from .relationship_ai import RelationshipAiError, RelationshipGuesser
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
COUNT_TIMEOUT_SECONDS = 45
"""The statement timeout of each count query: past it the source cancels the query."""
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
    confidence: float | None
    """``None`` for a ``manual`` relationship."""
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
        roles: RoleService | None = None,
        documents: PiiDocumentSearch | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._jobs = JobService(engine, runner=jobs, clock=clock)
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._connectors = connectors
        self._ai = WorkspaceAiService(engine, workspaces=workspaces, clock=clock)
        self._guesser = (
            RelationshipGuesser(engine, roles=roles, documents=documents)
            if roles is not None
            else None
        )
        """``None`` where the job does not run (the API's service): rules only."""

    # -- for users ----------------------------------------------------------------------

    def start_inference(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Job:
        """Queue an ``infer_relationships`` job (owners and editors). 409 ``no_snapshot``
        before the first Snapshot; 422 for a threshold outside (0, 1]."""
        self._workspaces.authorize(user, Action.RUN_PROFILING, workspace_id)
        if not 0 < threshold <= 1:
            raise ApiError(422, "invalid_threshold", "The threshold is above 0 and at most 1.")
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
                    "user_id": str(user.id),
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
                sa.or_(
                    RelationshipRecord.confidence.is_(None),
                    RelationshipRecord.confidence >= min_confidence - 1e-9,
                ),
            )
            if status is not None:
                query = query.where(RelationshipRecord.status == status)
            rows = db.execute(
                query.order_by(
                    RelationshipRecord.confidence.desc().nulls_last(),
                    RelationshipRecord.detected_at,
                )
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

    def create_manual(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        from_column_id: uuid.UUID,
        to_column_id: uuid.UUID,
    ) -> Relationship:
        """Add a relationship by hand (owners and editors): ``manual``, ``accepted``, no
        confidence. A pair already suggested or rejected becomes that manual row. 422
        ``same_column``; 409 ``declared_foreign_key`` and ``already_accepted``; 404 when a
        column is not a present column of a present table in the Source System."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        if from_column_id == to_column_id:
            raise ApiError(422, "same_column", "A column cannot reference itself.")
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            present = set(
                db.scalars(
                    sa.select(SrcColumnRecord.id)
                    .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(
                        SrcColumnRecord.id.in_((from_column_id, to_column_id)),
                        SrcDbSchemaRecord.source_system_id == system_id,
                        SrcColumnRecord.status == "present",
                        SrcTableRecord.status == "present",
                    )
                )
            )
            if present != {from_column_id, to_column_id}:
                raise _not_found("Column")
            snapshot_id = self._latest_snapshot(db, system_id)
            if snapshot_id is not None and self._is_declared(
                db, snapshot_id, from_column_id, to_column_id
            ):
                raise ApiError(
                    409,
                    "declared_foreign_key",
                    "The source database already declares this foreign key.",
                )
            record = db.scalars(
                sa.select(RelationshipRecord)
                .where(
                    sa.or_(
                        sa.and_(
                            RelationshipRecord.from_column_id == from_column_id,
                            RelationshipRecord.to_column_id == to_column_id,
                        ),
                        sa.and_(
                            RelationshipRecord.from_column_id == to_column_id,
                            RelationshipRecord.to_column_id == from_column_id,
                        ),
                    )
                )
                .order_by(RelationshipRecord.status != "accepted")
                .with_for_update()
            ).first()
            now = self._clock()
            old: dict[str, Any] | None = None
            if record is None:
                record = RelationshipRecord(
                    id=uuid.uuid4(), evidence={}, version=0, detected_at=now
                )
                db.add(record)
            elif record.status == "accepted":
                raise ApiError(
                    409, "already_accepted", "This pair is already an accepted relationship."
                )
            else:
                old = {"status": record.status, "origin": record.origin}
            record.from_column_id, record.to_column_id = from_column_id, to_column_id
            record.origin, record.confidence, record.status = "manual", None, "accepted"
            record.version += 1
            record.decided_by, record.decided_at = user.id, now
            db.flush()
            row = db.execute(self._select().where(RelationshipRecord.id == record.id)).one()
            record_audit(
                db,
                workspace_id=system.workspace_id,
                actor_id=user.id,
                entity_type="relationship",
                entity_id=record.id,
                old=old,
                new={"status": "accepted", "origin": "manual"},
                at=now,
            )
            record_activity(
                db,
                workspace_id=system.workspace_id,
                actor_id=user.id,
                verb="relationship.added",
                object_type="relationship",
                object_id=record.id,
                object_label=self._label(row),
                details={"source_system_id": str(system.id), "origin": "manual"},
                at=now,
            )
            return self._view(*row)

    def delete(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, relationship_id: uuid.UUID
    ) -> None:
        """Delete a relationship (owners and editors). A ``manual`` one is removed (the audit
        entry keeps its values); an inferred one is rejected, so it is never suggested
        again. A ``declared`` one cannot be deleted (409 ``declared_relationship``)."""
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
            if record.origin == "declared":
                raise ApiError(
                    409,
                    "declared_relationship",
                    "A foreign key in the source database can't be deleted here.",
                )
            if record.origin != "manual":
                self._apply_decision(db, system, row, user, "rejected")
                return
            now = self._clock()
            record_audit(
                db,
                workspace_id=system.workspace_id,
                actor_id=user.id,
                entity_type="relationship",
                entity_id=record.id,
                old={
                    "status": record.status,
                    "origin": record.origin,
                    "from_column_id": str(record.from_column_id),
                    "to_column_id": str(record.to_column_id),
                },
                new=None,
                at=now,
            )
            record_activity(
                db,
                workspace_id=system.workspace_id,
                actor_id=user.id,
                verb="relationship.deleted",
                object_type="relationship",
                object_id=record.id,
                object_label=self._label(row),
                details={"source_system_id": str(system.id), "origin": "manual"},
                at=now,
            )
            db.delete(record)

    # -- the job ------------------------------------------------------------------------

    def run_inference(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``infer_relationships`` job handler: guess, union, count, save."""
        system_id = uuid.UUID(params["source_system_id"])
        threshold = float(params.get("threshold", DEFAULT_THRESHOLD))
        user_id = uuid.UUID(params["user_id"]) if params.get("user_id") else None
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present":
                raise InferenceError("The Source System no longer exists.")
            workspace_id = system.workspace_id
            snapshot_id = self._latest_snapshot(db, system_id)
            if snapshot_id is None:
                raise InferenceError("The Source System has no Snapshot.")
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            dialect = _DIALECTS.get(connection.engine) if connection else None
            columns = self._columns(db, system_id, snapshot_id)
            declared = self._declared(db, snapshot_id, columns)
            joins = self._joins(db, snapshot_id, columns, dialect)
            excluded = self._excluded(db, system_id)
            params_for_source = self._source_params(connection) if connection else None
            engine_name = connection.engine if connection else None
        ctx.log(
            f"{len(columns)} columns in the latest Snapshot, {len(declared)} declared "
            f"foreign key pairs, {len(joins)} joined column pairs in views and routines."
        )
        ctx.raise_if_cancelled()
        rules = find_candidates(columns, joins, declared, threshold=threshold)
        ctx.log(f"The rules found {len(rules)} pairs (names and JOINs).")
        guesses = self._guess(workspace_id, user_id, snapshot_id, columns, threshold, ctx)
        candidates = union(rules, guesses, declared=declared, excluded=excluded)
        both = sum(1 for c in candidates if c.found_by == "both")
        ctx.log(f"{len(candidates)} pairs after the union, {both} found by both.")
        ctx.progress(30)
        if params_for_source is not None and engine_name is not None and dialect is not None:
            counting = replace(
                params_for_source,
                options={
                    **params_for_source.options,
                    "statement_timeout_seconds": COUNT_TIMEOUT_SECONDS,
                },
            )
            candidates = self._count(
                candidates, self._connectors(engine_name, counting), dialect, ctx
            )
        else:
            ctx.log("No live Connection: the pairs keep the score of their guess, uncounted.")
        kept = [c for c in candidates if c.confidence + 1e-9 >= threshold]
        created, updated, dropped = self._record(system_id, kept, threshold)
        ctx.log(
            f"Done: {len(kept)} relationships at or above {threshold:g} "
            f"({created} new, {updated} refreshed, {dropped} withdrawn)."
        )

    def _guess(
        self,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID | None,
        snapshot_id: uuid.UUID,
        columns: list[Col],
        threshold: float,
        ctx: JobContext,
    ) -> list[AiGuess]:
        """The AI's pairs; none, with the reason in the log, when it cannot guess."""
        if self._guesser is None:
            ctx.log("The AI is not set up for this job: the rules' pairs only.")
            return []
        try:
            guesses = self._guesser.guess(
                workspace_id=workspace_id,
                user_id=user_id,
                snapshot_id=snapshot_id,
                columns=columns,
                policy=self._ai.policy(workspace_id),
                threshold=threshold,
                ctx=ctx,
            )
        except RelationshipAiError as exc:
            ctx.log(f"The AI did not guess ({exc}): the rules' pairs only.")
            return []
        ctx.log(f"The AI found {len(guesses)} pairs.")
        return guesses

    # -- loading the model --------------------------------------------------------------

    def _columns(self, db: Session, system_id: uuid.UUID, snapshot_id: uuid.UUID) -> list[Col]:
        """The present columns of base tables in the Snapshot, with how their uniqueness is
        known."""
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
        return columns_out

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
                if a is None or b is None or a.id == b.id:
                    continue
                key = (a.id, b.id) if str(a.id) < str(b.id) else (b.id, a.id)
                found.setdefault(key, set()).add(label)
        return {key: sorted(labels) for key, labels in found.items()}

    def _excluded(self, db: Session, system_id: uuid.UUID) -> set[frozenset[uuid.UUID]]:
        """The rejected and manual pairs (either way round): never proposed or counted."""
        return {
            frozenset((from_id, to_id))
            for from_id, to_id in db.execute(
                sa.select(RelationshipRecord.from_column_id, RelationshipRecord.to_column_id)
                .join(SrcColumnRecord, SrcColumnRecord.id == RelationshipRecord.from_column_id)
                .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(
                    SrcDbSchemaRecord.source_system_id == system_id,
                    sa.or_(
                        RelationshipRecord.status == "rejected",
                        RelationshipRecord.origin == "manual",
                    ),
                )
            )
        }

    # -- counts -------------------------------------------------------------------------

    def _count(
        self, candidates: list[Candidate], connector: Connector, dialect: str, ctx: JobContext
    ) -> list[Candidate]:
        """Count every pair on the source, one query at a time, most promising first. A
        pair whose target turns out not unique is counted the other way round too, and the
        better direction is kept (``reverse_wins``). No time limit per run: the job stays
        cancellable between queries."""
        ordered = count_order(candidates)
        counted: list[Candidate] = []
        timed_out = failed = 0
        for done, candidate in enumerate(ordered, start=1):
            ctx.raise_if_cancelled()
            self._count_one(candidate, connector, dialect, ctx)
            chosen = candidate
            if candidate.counts is not None and not candidate.counts.target_unique:
                ctx.log(
                    f"{candidate.from_col.qualified} -> {candidate.to_col.qualified}: the target "
                    "is not unique; counting the other way round too."
                )
                reverse = candidate.reversed()
                self._count_one(reverse, connector, dialect, ctx)
                if reverse.counts is not None and reverse_wins(candidate.counts, reverse.counts):
                    chosen = reverse
            timed_out += chosen.count_status == "timed_out"
            failed += chosen.count_status == "failed"
            counted.append(chosen)
            if chosen is not candidate:
                ctx.log(
                    f"{candidate.from_col.qualified} -> {candidate.to_col.qualified}: the target "
                    "is not unique; counted the other way round, which reads better."
                )
            ctx.progress(30 + 60 * done // len(ordered))
        ctx.log(
            f"Counted {len(ordered)} pairs on the source: {timed_out} timed out, {failed} failed."
        )
        return counted

    @staticmethod
    def _count_one(
        candidate: Candidate, connector: Connector, dialect: str, ctx: JobContext
    ) -> None:
        """Run the pair's count query. A query past ``COUNT_TIMEOUT_SECONDS`` is cancelled
        by the source's statement timeout; any other failure keeps the guess."""
        label = f"{candidate.from_col.qualified} -> {candidate.to_col.qualified}"
        try:
            result = connector.query(
                count_sql(candidate.from_col, candidate.to_col, dialect), limit=1
            )
            [row] = result.rows
            candidate.counts = Counts(*(int(value or 0) for value in row[: len(COUNT_OUTPUTS)]))
            candidate.count_status = "counted"
        except ConnectorError as exc:
            if exc.code == "timeout":
                candidate.count_status = "timed_out"
                ctx.log(
                    f"{label}: count timed out after {COUNT_TIMEOUT_SECONDS} s and was "
                    "cancelled on the source."
                )
            else:
                candidate.count_status, candidate.count_reason = "failed", exc.message
                ctx.log(f"{label}: not counted ({exc.message}).")
        except ValueError:  # not exactly one row of counts
            candidate.count_status = "failed"
            candidate.count_reason = "The count query did not return one row of counts."
            ctx.log(f"{label}: not counted ({candidate.count_reason}).")

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
                    continue  # a rejected x -> y also suppresses y -> x
                if (record or reverse) is not None and "manual" in (
                    getattr(record, "origin", None),
                    getattr(reverse, "origin", None),
                ):
                    continue  # a manual pair is the user's: never scored, withdrawn or proposed
                if record is None and reverse is not None:
                    # The pair is now read the other way round: one row, never two. A
                    # suggestion turns round; an accepted pair keeps the direction decided.
                    seen.add((c.to_col.id, c.from_col.id))
                    record = reverse
                    if record.status == "suggested":
                        record.from_column_id, record.to_column_id = key
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
                    # A decision is never undone: only the score and its evidence move. The
                    # AI's evidence for a decision (``ai``) is not the rules' and stays, and
                    # a decided pair keeps the origin it was decided with.
                    record.confidence = c.confidence
                    kept_ai = (record.evidence or {}).get("ai")
                    record.evidence = c.evidence(threshold) | (
                        {"ai": kept_ai} if kept_ai is not None else {}
                    )
                    if record.status == "suggested":
                        record.origin = c.origin
                    updated += 1
            stale = [
                r.id
                for key, r in existing.items()
                if key not in seen
                and r.status == "suggested"
                and r.origin in ("inferred", "routine", "ai")
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

    @staticmethod
    def _label(row: Any) -> str:
        return f"{row[2].name}.{row[1].name} -> {row[5].name}.{row[4].name}"

    def _apply_decision(
        self, db: Session, system: SourceSystemRecord, row: Any, user: User, decision: str
    ) -> None:
        record: RelationshipRecord = row[0]
        old_status = record.status
        if old_status == decision:
            return
        now = self._clock()
        record.status = decision
        record.version += 1
        record.decided_by = user.id
        record.decided_at = now
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
            object_label=self._label(row),
            details={
                "source_system_id": str(system.id),
                "origin": record.origin,
                "confidence": record.confidence,
            },
            at=now,
        )

    def _is_declared(
        self, db: Session, snapshot_id: uuid.UUID, first: uuid.UUID, second: uuid.UUID
    ) -> bool:
        """Whether the Snapshot declares a foreign key between the two columns (either way)."""
        ends = {
            column_id: (table_id, name)
            for column_id, table_id, name in db.execute(
                sa.select(SrcColumnRecord.id, SrcColumnRecord.table_id, SrcColumnRecord.name).where(
                    SrcColumnRecord.id.in_((first, second))
                )
            )
        }
        for table_id, ref_table_id, names, ref_names in db.execute(
            sa.select(
                SnapshotConstraintRecord.src_table_id,
                SnapshotConstraintRecord.ref_table_id,
                SnapshotConstraintRecord.columns,
                SnapshotConstraintRecord.ref_columns,
            ).where(
                SnapshotConstraintRecord.snapshot_id == snapshot_id,
                SnapshotConstraintRecord.type == "fk",
                SnapshotConstraintRecord.src_table_id.in_([t for t, _ in ends.values()]),
                SnapshotConstraintRecord.ref_table_id.in_([t for t, _ in ends.values()]),
            )
        ):
            for name, ref_name in zip(names, ref_names, strict=False):
                if {(table_id, name), (ref_table_id, ref_name)} == set(ends.values()):
                    return True
        return False

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
            self._apply_decision(db, system, row, user, decision)
            db.flush()
            return self._view(*row)
