"""Data catalog suggestions (spec §6.19, #626): the AI suggests the empty descriptions,
classifications and SCD hints of a Source System's tables and the empty descriptions of its
columns; owners and editors accept or reject them one by one, never through a Change Set.

``start`` queues a ``catalog_suggest`` job for a whole Source System (owners and editors who
may ask the assistant); one runs per system at a time (an advisory lock and
``JobService.latest``: 409 ``catalog_suggestions_running``). ``run`` is its handler: it reads
the latest Snapshot's present tables, leaves out every field that has a value (a person's or
an accepted suggestion's), shows the model the rest with the values people rejected
(``catalog_ai``), keeps what is valid and new and replaces the pending suggestions of the run
before. Without an agent model it is skipped with a log line. Its result counts the
suggestions ``made`` and the fields ``skipped`` (they had a value, or the AI repeated a
rejected value).

Accepting copies the value onto ``src_tables`` / ``src_columns`` through the enhancement code
(validated, versioned, audited ``via=ai`` and in the activity feed) and marks the field as the
AI's (``ai_fields``) until anyone changes it. Edit-then-accept saves the person's value
instead, audited ``via=user``, with no AI mark. A rejection is audited and its value is never
suggested again for that object and field. An extraction or import expires the pending
suggestions of objects the new Snapshot lacks (``internal.snapshots``). Viewers get 403 on
every method here: they see accepted values in the Source Schema only.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.llm import (
    DataSharingLevel,
    DataSharingPolicy,
    RoleService,
    ToolCall,
    WorkspaceAiService,
)
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .catalog_ai import (
    DISTINCT_TOOL,
    SCD_CLASSIFICATIONS,
    SEARCH_TOOL,
    TABLES_PER_BATCH,
    CatalogAiError,
    CatalogTable,
    Suggested,
    ask,
    normalised,
    tool_specs,
    validate,
)
from .enhancement_service import write_enhancements
from .internal.connector import ConnectionParams, Connector
from .internal.pii import id_chunks, protected_column_ids
from .pii_ai_service import PiiDocumentSearch
from .source_query_service import QueryBudget, SourceQueryService
from .tables import (
    CATALOG_SUGGESTION_STATUSES,
    CatalogSuggestionRecord,
    ColumnProfileRecord,
    ConnectionRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotRecord,
    SnapshotTableRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

CATALOG_SUGGEST_JOB = "catalog_suggest"
MODEL_ROLE = "agent"
ACTIVE = ("queued", "running", "paused")
PASSAGES_PER_SEARCH = 5
DISTINCT_LIMIT = 50
"""The most distinct values one ``distinct_values`` call shows the model."""
_CODE_TYPE = re.compile(r"char|text|string|int|number|numeric|decimal", re.IGNORECASE)
"""Data types a code column may have: characters or whole numbers."""


@dataclass(frozen=True)
class CatalogSuggestion:
    id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column_id: uuid.UUID | None
    column: str | None
    field: str
    value: str
    """What the AI suggested."""
    edited_value: str | None
    """What a person accepted instead, when they edited it."""
    current_value: str | None
    """The object's value for the field now."""
    status: str
    job_id: uuid.UUID | None
    created_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


@dataclass(frozen=True)
class CatalogSuggestionPage:
    items: list[CatalogSuggestion]
    counts: dict[str, int]
    """Every status of the Source System's suggestions (no filter) -> how many."""


@dataclass(frozen=True)
class Decided:
    decided: list[CatalogSuggestion]
    skipped: list[uuid.UUID]
    """Asked for, but no longer pending (decided already or expired)."""


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class CatalogSuggestionService:
    """Every method that acts for a user authorizes through the workspaces policy first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        jobs: JobRunner,
        clock: Clock,
        roles: RoleService | None = None,
        documents: PiiDocumentSearch | None = None,
        users: Callable[[uuid.UUID], User | None] | None = None,
        encryption_key: bytes | None = None,
        connectors: Callable[[str, ConnectionParams], Connector] | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._jobs = JobService(engine, runner=jobs, clock=clock)
        self._clock = clock
        self._roles = roles
        """``None`` where the job does not run (the API's service)."""
        self._documents = documents
        self._users = users
        self._ai = WorkspaceAiService(engine, workspaces=workspaces, clock=clock)
        self._queries = (
            SourceQueryService(
                engine,
                workspaces=workspaces,
                encryption_key=encryption_key,
                clock=clock,
                connectors=connectors,
            )
            if encryption_key is not None
            else None
        )

    # -- the job, for users -------------------------------------------------------------

    def start(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Job:
        """Queue a ``catalog_suggest`` job for the whole Source System (owners and editors who
        may ask the assistant). 409 ``no_snapshot`` before the first Snapshot; 409
        ``catalog_suggestions_running`` while one runs for it already."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        scope = {"source_system_id": str(system_id)}
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            if _latest_snapshot(db, system_id) is None:
                raise ApiError(
                    409,
                    "no_snapshot",
                    "Catalog entries are suggested from a Snapshot: extract or import the "
                    "schema first.",
                )
            # One job per Source System: two clicks at once queue one job.
            key = f"catalog_suggest:{workspace_id}:{system_id}"
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
            current = self._jobs.latest(workspace_id, CATALOG_SUGGEST_JOB, scope, db=db)
            if current is not None and current.status in ACTIVE:
                raise ApiError(
                    409,
                    "catalog_suggestions_running",
                    "Catalog entries are already being suggested for this Source System.",
                    {"job_id": str(current.id)},
                )
            return self._jobs.submit(
                workspace_id,
                CATALOG_SUGGEST_JOB,
                {**scope, "workspace_id": str(workspace_id), "user_id": str(user.id)},
                title=f"Suggest catalog entries: {system.name}",
                created_by=user.id,
                db=db,
            )

    def latest(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Job | None:
        """The newest ``catalog_suggest`` job of the Source System (owners and editors), or
        ``None``."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
        return self._jobs.latest(
            workspace_id, CATALOG_SUGGEST_JOB, {"source_system_id": str(system_id)}
        )

    # -- the suggestions ----------------------------------------------------------------

    def list_suggestions(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        status: str = "pending",
        table_id: uuid.UUID | None = None,
    ) -> CatalogSuggestionPage:
        """The Source System's suggestions in ``status``, of one table when ``table_id`` is
        given, by table then table fields before columns (owners and editors), and the count
        of each status."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        if status not in CATALOG_SUGGESTION_STATUSES:
            raise ApiError(
                422,
                "invalid_status",
                f"The status is one of {', '.join(CATALOG_SUGGESTION_STATUSES)}.",
            )
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            query = sa.select(CatalogSuggestionRecord).where(
                CatalogSuggestionRecord.source_system_id == system_id,
                CatalogSuggestionRecord.status == status,
            )
            if table_id is not None:
                query = query.where(CatalogSuggestionRecord.src_table_id == table_id)
            items = self._views(db, list(db.scalars(query)))
            counts = dict.fromkeys(CATALOG_SUGGESTION_STATUSES, 0)
            counts.update(
                db.execute(
                    sa.select(CatalogSuggestionRecord.status, sa.func.count())
                    .where(CatalogSuggestionRecord.source_system_id == system_id)
                    .group_by(CatalogSuggestionRecord.status)
                ).tuples()
            )
        return CatalogSuggestionPage(items=items, counts=counts)

    def accept(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        ids: Sequence[uuid.UUID],
    ) -> Decided:
        """Accept pending suggestions as the AI wrote them (owners and editors): each value is
        copied onto its object (audited ``via=ai``) and marked as the AI's. Suggestions no
        longer pending are skipped; 404 for an id not of this Source System."""
        return self._decide(user, workspace_id, system_id, ids, "accepted")

    def accept_all(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        table_id: uuid.UUID | None = None,
    ) -> Decided:
        """Accept every pending suggestion of the Source System, or of one table."""
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        with Session(self._engine) as db:
            query = sa.select(CatalogSuggestionRecord.id).where(
                CatalogSuggestionRecord.source_system_id == system_id,
                CatalogSuggestionRecord.status == "pending",
            )
            if table_id is not None:
                query = query.where(CatalogSuggestionRecord.src_table_id == table_id)
            ids = list(db.scalars(query))
        return self._decide(user, workspace_id, system_id, ids, "accepted")

    def accept_edited(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        value: str,
    ) -> CatalogSuggestion:
        """Accept one pending suggestion with a person's value instead (owners and editors):
        a person's value, audited ``via=user``, with no AI mark. 409
        ``suggestion_not_pending`` when it was decided or expired; 422
        ``invalid_enhancement`` for a value the field does not take."""
        decided = self._decide(
            user, workspace_id, system_id, [suggestion_id], "accepted", edited=value
        )
        if not decided.decided:
            raise ApiError(
                409,
                "suggestion_not_pending",
                "This suggestion was already decided, or it expired.",
            )
        return decided.decided[0]

    def reject(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        ids: Sequence[uuid.UUID],
    ) -> Decided:
        """Reject pending suggestions (owners and editors), audited: their values are never
        suggested again for that object and field. Others are skipped as in ``accept``."""
        return self._decide(user, workspace_id, system_id, ids, "rejected")

    def _decide(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        ids: Sequence[uuid.UUID],
        status: str,
        *,
        edited: str | None = None,
    ) -> Decided:
        self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
        wanted = list(dict.fromkeys(ids))
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            records = {
                r.id: r
                for chunk in id_chunks(wanted)
                for r in db.scalars(
                    sa.select(CatalogSuggestionRecord)
                    .where(
                        CatalogSuggestionRecord.id.in_(chunk),
                        CatalogSuggestionRecord.source_system_id == system_id,
                    )
                    .order_by(CatalogSuggestionRecord.id)
                    .with_for_update()
                )
            }
            if len(records) != len(wanted):
                raise _not_found("Suggestion")
            decided: list[CatalogSuggestionRecord] = []
            skipped: list[uuid.UUID] = []
            for suggestion_id in wanted:
                record = records[suggestion_id]
                if record.status != "pending":
                    skipped.append(record.id)
                    continue
                if status == "accepted" and not self._apply(db, user, system, record, edited, now):
                    skipped.append(record.id)  # its object is gone: it expired
                    continue
                record.status = status
                record.decided_by = user.id
                record.decided_at = now
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="catalog_suggestion",
                    entity_id=record.id,
                    old={"status": "pending"},
                    new={"status": status}
                    | ({"edited_value": record.edited_value} if record.edited_value else {}),
                    at=now,
                )
                decided.append(record)
            db.flush()
            return Decided(decided=self._views(db, decided), skipped=skipped)

    def _apply(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        suggestion: CatalogSuggestionRecord,
        edited: str | None,
        now: datetime,
    ) -> bool:
        """Copy an accepted suggestion's value onto its object; ``False`` (and the suggestion
        expired) when the object is no longer present."""
        target: SrcTableRecord | SrcColumnRecord | None
        if suggestion.src_column_id is not None:
            target = db.get(SrcColumnRecord, suggestion.src_column_id, with_for_update=True)
            entity_type = "source_column"
        else:
            target = db.get(SrcTableRecord, suggestion.src_table_id, with_for_update=True)
            entity_type = "source_table"
        if target is None or target.status != "present":
            suggestion.status = "expired"
            return False
        person = edited is not None and edited.strip() != suggestion.value
        value = edited.strip() if person and edited is not None else suggestion.value
        if suggestion.field == "classification":
            value = value.casefold()
        if person:
            suggestion.edited_value = value
        changed = write_enhancements(
            db,
            actor_id=user.id,
            system=system,
            record=target,
            entity_type=entity_type,
            changes={suggestion.field: value},
            at=now,
            via="user" if person else "ai",
        )
        if changed and not person:
            target.ai_fields = [*(target.ai_fields or []), suggestion.field]
        return True

    def _views(
        self, db: Session, records: Sequence[CatalogSuggestionRecord]
    ) -> list[CatalogSuggestion]:
        table_ids = list({r.src_table_id for r in records})
        column_ids = list({r.src_column_id for r in records if r.src_column_id is not None})
        tables = {
            t.id: (t, schema)
            for chunk in id_chunks(table_ids)
            for t, schema in db.execute(
                sa.select(SrcTableRecord, SrcDbSchemaRecord.name)
                .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                .where(SrcTableRecord.id.in_(chunk))
            ).tuples()
        }
        columns = {
            c.id: c
            for chunk in id_chunks(column_ids)
            for c in db.scalars(sa.select(SrcColumnRecord).where(SrcColumnRecord.id.in_(chunk)))
        }
        order = {"description": 0, "classification": 1, "scd_hint": 2}
        views: list[CatalogSuggestion] = []
        for r in records:
            table, schema = tables[r.src_table_id]
            column = columns.get(r.src_column_id) if r.src_column_id is not None else None
            target: Any = column if column is not None else table
            views.append(
                CatalogSuggestion(
                    id=r.id,
                    table_id=r.src_table_id,
                    db_schema=schema,
                    table=table.name,
                    column_id=r.src_column_id,
                    column=column.name if column is not None else None,
                    field=r.field,
                    value=r.value,
                    edited_value=r.edited_value,
                    current_value=getattr(target, r.field),
                    status=r.status,
                    job_id=r.job_id,
                    created_at=r.created_at,
                    decided_by=r.decided_by,
                    decided_at=r.decided_at,
                )
            )
        views.sort(
            key=lambda v: (
                v.db_schema,
                v.table,
                v.column is not None,
                v.column or "",
                order.get(v.field, 9),
            )
        )
        return views

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        system = db.get(SourceSystemRecord, system_id)
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    # -- the job ------------------------------------------------------------------------

    def run(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``catalog_suggest`` job handler (see the module docstring)."""
        system_id = uuid.UUID(str(params["source_system_id"]))
        user_id = uuid.UUID(str(params["user_id"]))
        user = self._users(user_id) if self._users is not None else None
        if user is None or not user.is_active:
            raise RuntimeError("The member who started this job can no longer sign in.")
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present":
                raise RuntimeError("The Source System no longer exists.")
            workspace_id = system.workspace_id
            snapshot_id = _latest_snapshot(db, system_id)
            has_connection = (
                db.scalar(
                    sa.select(ConnectionRecord.id).where(
                        ConnectionRecord.source_system_id == system_id
                    )
                )
                is not None
            )
        try:
            self._workspaces.authorize(user, Action.EDIT_SOURCE_ENHANCEMENTS, workspace_id)
            self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        except ApiError as exc:
            raise RuntimeError(exc.message) from exc
        if snapshot_id is None:
            raise RuntimeError("The Source System has no Snapshot.")
        policy = self._ai.policy(workspace_id)
        with Session(self._engine) as db:
            tables, filled, columns = _catalog(db, system_id, snapshot_id, policy)
        asked = [t for t in tables if t.fill or t.columns]
        ctx.log(
            f"{len(tables)} tables in the latest Snapshot; {len(asked)} have empty fields, "
            f"{filled} fields already have a value."
        )
        if not asked:
            self._save(system_id, [], set(), ctx)
            ctx.set_result({"made": 0, "skipped": filled})
            return
        if self._roles is None:
            raise RuntimeError("The AI is not set up for this job.")
        try:
            gateway = self._roles.gateway_for_role(
                MODEL_ROLE, workspace_id=workspace_id, user_id=user.id
            )
        except ApiError as exc:
            if exc.code != "model_role_unassigned":
                raise RuntimeError(exc.message) from exc
            ctx.log(
                "Skipped: this Workspace has no agent model, so the AI did not suggest catalog "
                "entries. An owner chooses one in the Workspace's AI settings."
            )
            ctx.set_result({"made": 0, "skipped": filled})
            return
        gateway.on_fall_back = lambda failed, error, model: ctx.log(
            f"{model} took over from {failed}, which failed."
        )
        use_documents = (
            self._documents is not None
            and policy.allows(DataSharingLevel.DOCUMENTS)
            and self._documents.available(workspace_id)
        )
        use_distinct = (
            self._queries is not None
            and has_connection
            and policy.allows(DataSharingLevel.SAMPLES)
            and self._may_query(user, workspace_id)
        )
        tools = tool_specs(documents=use_documents, distinct=use_distinct)
        budget = QueryBudget()

        def run_tool(call: ToolCall) -> str:
            if call.name == SEARCH_TOOL and use_documents:
                return self._search(call, user.id, workspace_id)
            if call.name == DISTINCT_TOOL and use_distinct:
                return self._distinct(call, user, workspace_id, system_id, columns, budget, ctx)
            return json.dumps({"error": f"There is no tool called {call.name}."})

        batches = [asked[i : i + TABLES_PER_BATCH] for i in range(0, len(asked), TABLES_PER_BATCH)]
        suggestions: list[Suggested] = []
        repeated = 0
        not_reached: set[uuid.UUID] = set()
        for number, batch in enumerate(batches, start=1):
            ctx.raise_if_cancelled()
            try:
                kept = validate(ask(gateway, batch, tools, run_tool), batch)
            except CatalogAiError as exc:
                if number == 1:
                    raise RuntimeError(f"The AI could not suggest catalog entries: {exc}") from None
                ctx.log(f"The AI stopped at batch {number} of {len(batches)}: {exc}")
                not_reached = {t.id for b in batches[number - 1 :] for t in b}
                break
            suggestions.extend(kept.suggestions)
            repeated += kept.repeated
            ctx.progress(min(95, number * 100 // len(batches)))
        made, late = self._save(system_id, suggestions, not_reached, ctx)
        skipped = filled + repeated + late
        ctx.log(
            f"Done: {made} suggestions; skipped {skipped} fields that already had a value or "
            "whose suggestion people rejected before."
        )
        ctx.set_result({"made": made, "skipped": skipped})

    def _save(
        self,
        system_id: uuid.UUID,
        suggestions: Sequence[Suggested],
        not_reached: set[uuid.UUID],
        ctx: JobContext,
    ) -> tuple[int, int]:
        """Replace the pending suggestions of the run before (but those of tables the AI did
        not reach) with ``suggestions``, leaving out a field someone filled while the model
        thought. How many were made, and how many were left out so."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            delete = sa.delete(CatalogSuggestionRecord).where(
                CatalogSuggestionRecord.source_system_id == system_id,
                CatalogSuggestionRecord.status == "pending",
            )
            if not_reached:
                delete = delete.where(CatalogSuggestionRecord.src_table_id.not_in(not_reached))
            db.execute(delete)
            tables = {
                t.id: t
                for chunk in id_chunks(list({s.table_id for s in suggestions}))
                for t in db.scalars(sa.select(SrcTableRecord).where(SrcTableRecord.id.in_(chunk)))
            }
            columns = {
                c.id: c
                for chunk in id_chunks(
                    list({s.column_id for s in suggestions if s.column_id is not None})
                )
                for c in db.scalars(sa.select(SrcColumnRecord).where(SrcColumnRecord.id.in_(chunk)))
            }
            made = late = 0
            for s in suggestions:
                target: Any = (
                    columns.get(s.column_id) if s.column_id is not None else tables.get(s.table_id)
                )
                if target is None or target.status != "present":
                    continue
                if getattr(target, s.field) is not None:
                    late += 1
                    continue
                db.add(
                    CatalogSuggestionRecord(
                        source_system_id=system_id,
                        src_table_id=s.table_id,
                        src_column_id=s.column_id,
                        field=s.field,
                        value=s.value,
                        status="pending",
                        job_id=ctx.job_id,
                        created_at=now,
                    )
                )
                made += 1
        return made, late

    def _may_query(self, user: User, workspace_id: uuid.UUID) -> bool:
        try:
            self._workspaces.authorize(user, Action.RUN_SOURCE_QUERY, workspace_id)
        except ApiError:
            return False
        return True

    def _search(self, call: ToolCall, user_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
        assert self._documents is not None
        query = str(call.arguments.get("query") or "").strip()[:500]
        if not query:
            return json.dumps({"error": "Give a query."})
        try:
            passages = self._documents.search(user_id, workspace_id, query, PASSAGES_PER_SEARCH)
        except ApiError as exc:
            return json.dumps({"error": exc.message})
        if not passages:
            return json.dumps({"passages": [], "note": "Nothing relevant in the documents."})
        return json.dumps({"passages": passages}, ensure_ascii=False)

    def _distinct(
        self,
        call: ToolCall,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        columns: Mapping[str, _Column],
        budget: QueryBudget,
        ctx: JobContext,
    ) -> str:
        """The distinct values of a non-protected code column, through the guarded source
        query (guard, masking, the run's budget, an audit entry); recorded on the job."""
        assert self._queries is not None
        ref = str(call.arguments.get("column") or "").strip()
        column = columns.get(ref) or columns.get(ref.casefold())
        if column is None:
            return json.dumps({"error": "There is no such column: name it schema.table.column."})
        if column.protected:
            return json.dumps({"error": "That column holds personal data: it is not queried."})
        if not _CODE_TYPE.search(column.data_type or ""):
            return json.dumps({"error": "Only code columns (text or whole numbers) are queried."})
        sql = _distinct_sql(column, self._engine_of(system_id))
        try:
            result = self._queries.run(user, workspace_id, system_id, sql, budget=budget)
        except ApiError as exc:
            ctx.log(f"Source query ({exc.code}): {redact_text(sql, entities=False)}")
            return json.dumps({"error": exc.message})
        ctx.log(
            f"Source query ({result.row_count} rows, {result.duration_ms} ms): "
            f"{redact_text(sql, entities=False)}"
        )
        if result.masked_columns:
            return json.dumps({"error": "That column holds personal data: its values are masked."})
        values = [row[0] for row in result.rows[:DISTINCT_LIMIT]]
        return json.dumps(
            {"column": ref, "values": values, "truncated": result.row_count > len(values)},
            ensure_ascii=False,
            default=str,
        )

    def _engine_of(self, system_id: uuid.UUID) -> str:
        with Session(self._engine) as db:
            return (
                db.scalar(
                    sa.select(ConnectionRecord.engine).where(
                        ConnectionRecord.source_system_id == system_id
                    )
                )
                or "postgresql"
            )


@dataclass(frozen=True)
class _Column:
    schema: str
    table: str
    name: str
    data_type: str | None
    protected: bool


def _distinct_sql(column: _Column, engine: str) -> str:
    """``SELECT DISTINCT`` of one column with a row bound, quoted for ``engine``."""

    def quote(name: str) -> str:
        if engine == "mysql":
            return "`" + name.replace("`", "``") + "`"
        if engine == "sqlserver":
            return "[" + name.replace("]", "]]") + "]"
        return '"' + name.replace('"', '""') + '"'

    col = quote(column.name)
    source = f"{quote(column.schema)}.{quote(column.table)}"
    if engine == "sqlserver":
        return f"SELECT DISTINCT TOP {DISTINCT_LIMIT} {col} FROM {source}"
    if engine == "oracle":
        return f"SELECT DISTINCT {col} FROM {source} FETCH FIRST {DISTINCT_LIMIT} ROWS ONLY"
    return f"SELECT DISTINCT {col} FROM {source} LIMIT {DISTINCT_LIMIT}"


def _latest_snapshot(db: Session, system_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(
        sa.select(SnapshotRecord.id).where(
            SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
        )
    )


def _catalog(
    db: Session, system_id: uuid.UUID, snapshot_id: uuid.UUID, policy: DataSharingPolicy
) -> tuple[list[CatalogTable], int, dict[str, _Column]]:
    """Every table of the latest Snapshot as the model is shown it, with its empty fields and
    rejected values; how many fields already have a value; every column by
    ``schema.table.column`` (and its lower case), for ``distinct_values``."""
    tables = list(
        db.execute(
            sa.select(SrcTableRecord, SrcDbSchemaRecord.name)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(
                SrcTableRecord.id.in_(
                    sa.select(SnapshotTableRecord.src_table_id).where(
                        SnapshotTableRecord.snapshot_id == snapshot_id
                    )
                ),
                SrcTableRecord.status == "present",
            )
            .order_by(SrcDbSchemaRecord.name, SrcTableRecord.name)
        ).tuples()
    )
    column_ids = sa.select(SnapshotColumnRecord.src_column_id).where(
        SnapshotColumnRecord.snapshot_id == snapshot_id
    )
    by_table: dict[uuid.UUID, list[SrcColumnRecord]] = {}
    for column in db.scalars(
        sa.select(SrcColumnRecord)
        .where(SrcColumnRecord.id.in_(column_ids), SrcColumnRecord.status == "present")
        .order_by(SrcColumnRecord.name)
    ):
        by_table.setdefault(column.table_id, []).append(column)
    protected = protected_column_ids(db, column_ids)
    profiles: dict[uuid.UUID, tuple[int | None, float | None, int | None]] = {}
    if policy.allows(DataSharingLevel.PROFILES):
        profiles = {
            column_id: (rows, null_pct, distinct)
            for column_id, rows, null_pct, distinct in db.execute(
                sa.select(
                    ColumnProfileRecord.src_column_id,
                    ColumnProfileRecord.row_count,
                    ColumnProfileRecord.null_pct,
                    ColumnProfileRecord.distinct_count,
                ).where(ColumnProfileRecord.src_column_id.in_(column_ids))
            ).tuples()
            if column_id not in protected
        }
    constraints: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for constraint in db.scalars(
        sa.select(SnapshotConstraintRecord)
        .where(SnapshotConstraintRecord.snapshot_id == snapshot_id)
        .order_by(SnapshotConstraintRecord.name)
    ):
        entry: dict[str, Any] = {"type": constraint.type, "columns": constraint.columns}
        if constraint.ref_table:
            entry["references"] = (
                f"{constraint.ref_db_schema}.{constraint.ref_table}"
                f"({', '.join(constraint.ref_columns)})"
            )
        constraints.setdefault(constraint.src_table_id, []).append(entry)
    rejected: dict[tuple[uuid.UUID, uuid.UUID | None, str], list[str]] = {}
    for suggestion in db.scalars(
        sa.select(CatalogSuggestionRecord)
        .where(
            CatalogSuggestionRecord.source_system_id == system_id,
            CatalogSuggestionRecord.status == "rejected",
        )
        .order_by(CatalogSuggestionRecord.created_at)
    ):
        rejected.setdefault(
            (suggestion.src_table_id, suggestion.src_column_id, suggestion.field), []
        ).append(suggestion.value)

    filled = 0
    out: list[CatalogTable] = []
    refs: dict[str, _Column] = {}
    for table, schema in tables:
        ref = f"{schema}.{table.name}"
        fill: list[str] = []
        for name in ("description", "classification"):
            if getattr(table, name) is None:
                fill.append(name)
            else:
                filled += 1
        if table.scd_hint is not None:
            filled += 1
        elif table.classification is None or table.classification in SCD_CLASSIFICATIONS:
            fill.append("scd_hint")
        shown_columns: list[dict[str, Any]] = []
        empty: dict[str, uuid.UUID] = {}
        rows: int | None = None
        for column in by_table.get(table.id, []):
            definition = column.current_definition or {}
            refs[f"{ref}.{column.name}"] = _Column(
                schema, table.name, column.name, definition.get("data_type"), column.id in protected
            )
            refs.setdefault(f"{ref}.{column.name}".casefold(), refs[f"{ref}.{column.name}"])
            if column.description is None:
                empty[column.name] = column.id
            else:
                filled += 1
            profile = profiles.get(column.id)
            stats = None
            if profile is not None:
                count, null_pct, distinct = profile
                rows = count if rows is None or (count or 0) > rows else rows
                stats = _without_none(
                    {
                        "null_pct": round(null_pct, 1) if null_pct is not None else None,
                        "distinct_pct": round(100 * distinct / count, 1)
                        if distinct is not None and count
                        else None,
                    }
                )
            # Only the shape of the column: never its default, which can be a value.
            shown_columns.append(
                _without_none(
                    {
                        "name": column.name,
                        "data_type": definition.get("data_type"),
                        "nullable": definition.get("is_nullable"),
                        "primary_key": definition.get("is_pk") or None,
                        "comment": definition.get("comment"),
                        "description": column.description,
                        "profile": stats or None,
                    }
                )
            )
        table_rejected = {
            (column_id, name): values
            for (table_id, column_id, name), values in rejected.items()
            if table_id == table.id
        }
        names = {c.id: c.name for c in by_table.get(table.id, [])}
        shown_rejected = [
            _without_none({"column": names.get(column_id), "field": name, "value": value})
            for (column_id, name), values in table_rejected.items()
            if (column_id is None and name in fill) or (column_id in empty.values())
            for value in values
        ]
        shown = _without_none(
            {
                "table": ref,
                "kind": table.kind,
                "comment": (table.current_definition or {}).get("comment"),
                "description": table.description,
                "classification": table.classification,
                "scd_hint": table.scd_hint,
                "rows": rows,
                "constraints": constraints.get(table.id),
                "columns": shown_columns,
                "fill": {"table": fill, "columns": sorted(empty)},
                "rejected": shown_rejected or None,
            }
        )
        out.append(
            CatalogTable(
                id=table.id,
                ref=ref,
                shown=shown,
                classification=table.classification,
                fill=fill,
                columns=empty,
                rejected={
                    key: {normalised(v) for v in values} for key, values in table_rejected.items()
                },
            )
        )
    return out, filled, refs


def _without_none(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v is not None}
