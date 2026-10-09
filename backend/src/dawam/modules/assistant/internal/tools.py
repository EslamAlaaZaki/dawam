"""The assistant's tool registry (spec §6.16).

Tools call the same service layer as the REST API, as the requesting user. Each tool names
the policy ``Action`` that governs it, and every call is authorized through
``WorkspaceService.authorize`` (which asks ``can``) before it runs: a viewer's chat can only
use tools whose action a viewer may perform, whatever the model asks for. A tool that puts
Workspace data in front of the model also names the data-sharing level it needs. The tools
that read a Source System never put a Protected Column's value in front of the model: such a
column goes out by name and PII category only.
Every object a tool returns carries a ``link`` (an app path) for the answer to cite.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from dawam.modules.auth import User
from dawam.modules.changesets import ChangeSetService, ProposedItem
from dawam.modules.files import DocumentSearchService, FileService
from dawam.modules.jobs import JobService
from dawam.modules.kpis import KpiService
from dawam.modules.llm import DataSharingLevel, DataSharingPolicy, ToolSpec
from dawam.modules.sources import (
    DEFAULT_BUDGET_SECONDS,
    PiiService,
    ProfilingService,
    QueryBudget,
    SnapshotService,
    SourceQueryService,
)
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .agent import ToolOutcome

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 20_000
MAX_FILE_TEXT_CHARS = 15_000
MAX_PROPOSED_ITEMS = 200
MAX_JOB_ITEMS = 1000
ASSISTANT_JOB = "assistant_task"
"""The background job type that runs a long assistant task."""


@dataclass(frozen=True)
class ToolServices:
    kpis: KpiService
    files: FileService
    change_sets: ChangeSetService
    snapshots: SnapshotService
    profiling: ProfilingService
    pii: PiiService
    documents: DocumentSearchService
    source_queries: SourceQueryService
    jobs: JobService


@dataclass(frozen=True)
class ToolContext:
    user: User
    workspace_id: uuid.UUID
    services: ToolServices
    policy: DataSharingPolicy
    """What the Workspace lets the model see; a tool that may send more or less than its own
    ``level`` asks it."""
    conversation_id: uuid.UUID | None = None
    budget: QueryBudget = field(default_factory=QueryBudget)
    """The source-query seconds this run has left (one budget per run)."""
    proposals: Proposals | None = None
    """Set for a job: ``propose_changes`` collects here instead of proposing at once."""


@dataclass(frozen=True)
class Reported:
    """A tool result with the part of it that may be saved on the run: for a source query,
    never the rows."""

    content: Any
    saved: dict[str, Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    kind: Literal["read", "write"]
    action: Action
    """The policy action the call is checked against, as the requesting user."""
    run: Callable[[ToolContext, Any], Any]
    level: DataSharingLevel | None = None
    """The data-sharing level the Workspace needs for the tool to be used at all."""
    chat_only: bool = False
    """Not offered to a job (a job does not start jobs)."""

    @property
    def spec(self) -> ToolSpec:
        schema = self.arguments.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


SYSTEM_ID = "The Source System's id."


class _SearchCatalog(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    query: str = Field(min_length=1, max_length=200, description="Part of an object's name.")
    limit: int = Field(default=20, ge=1, le=50)


class _GetObject(BaseModel):
    kind: Literal["kpi", "table", "column", "routine"] = Field(description="The kind of object.")
    id: uuid.UUID = Field(description="The object's id.")
    system_id: uuid.UUID | None = Field(
        default=None, description="The Source System's id; needed for a table, column or routine."
    )


class _GetProfile(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    table_id: uuid.UUID
    column_id: uuid.UUID | None = Field(
        default=None, description="Only this column; leave out for the whole table."
    )


class _GetSnapshotDiff(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    snapshot_id: uuid.UUID | None = Field(
        default=None, description="The newer Snapshot; leave out for the latest."
    )
    against_id: uuid.UUID | None = Field(
        default=None, description="The older Snapshot; leave out for the one before."
    )


class _GetPiiFindings(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    status: Literal["suggested", "confirmed", "dismissed"] | None = None


class _SearchDocuments(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    system_id: uuid.UUID | None = Field(
        default=None, description="Only this Source System's documents."
    )
    limit: int = Field(default=5, ge=1, le=20)


class _ListFiles(BaseModel):
    system_id: uuid.UUID | None = Field(
        default=None,
        description="A Source System's id; leave out for the Data Warehouse's file area.",
    )


class _ReadFile(BaseModel):
    file_id: uuid.UUID


class _GenerateFile(BaseModel):
    system_id: uuid.UUID = Field(description="The Source System whose file area gets the file.")
    name: str = Field(min_length=1, max_length=200, description="File name, e.g. `notes.md`.")
    content: str = Field(max_length=200_000, description="The file's text.")


class _RunSourceQuery(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    sql: str = Field(
        min_length=1,
        max_length=4000,
        description="One SELECT over the Source System's tables, e.g. a COUNT(*) overlap check. "
        "Name tables as schema.table. Protected columns can be counted and joined on, not shown.",
    )


class _ChangeItem(BaseModel):
    key: str = Field(
        min_length=1, max_length=40, description="A short name for this item, for `depends_on`."
    )
    object_type: Literal["source_table", "source_column"] = Field(
        description="`source_table` or `source_column`."
    )
    object_id: uuid.UUID = Field(description="The table's or column's id.")
    changes: dict[str, Any] = Field(
        min_length=1,
        description="The fields to change and their new values. Tables and columns: "
        "`description` (text), `tags` (list of text), `is_sensitive` (boolean). Tables only: "
        "`classification` (master, transactional, reference, log or landing) and `scd_hint` "
        "(text).",
    )
    label: str = Field(
        default="", max_length=200, description="What to call the object, e.g. `core.customers`."
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Keys of earlier items this one needs."
    )


class _ProposeChanges(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="What the changes are for.")
    source_system_id: uuid.UUID = Field(description="The Source System the objects belong to.")
    items: list[_ChangeItem] = Field(min_length=1, max_length=MAX_PROPOSED_ITEMS)


class _StartJob(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="A short name for the job.")
    task: str = Field(
        min_length=1,
        max_length=4000,
        description="The whole task, self-contained: the job does not see this conversation.",
    )


class Proposals:
    """The changes a job proposes: held until the job succeeds, then made one Change Set.
    A job that aborts discards them, so it leaves no Change Set behind."""

    def __init__(self) -> None:
        self.source_system_id: uuid.UUID | None = None
        self.items: list[_ChangeItem] = []

    def add(self, source_system_id: uuid.UUID, items: Sequence[_ChangeItem]) -> int:
        self.check(source_system_id, items)
        self.source_system_id = source_system_id
        self.items.extend(items)
        return len(self.items)

    def check(self, source_system_id: uuid.UUID, items: Sequence[_ChangeItem]) -> None:
        if self.source_system_id not in (None, source_system_id):
            raise ApiError(
                422,
                "one_source_system",
                "A job's changes go into one Change Set, so they are all for one Source System.",
            )
        taken = {item.key for item in self.items}
        for item in items:
            if item.key in taken:
                raise ApiError(422, "duplicate_key", f"The key `{item.key}` is already used.")
            taken.add(item.key)
        if len(self.items) + len(items) > MAX_JOB_ITEMS:
            raise ApiError(422, "too_many_items", "This job has proposed too many changes.")


def _proposed_items(items: Sequence[_ChangeItem]) -> list[ProposedItem]:
    return [
        ProposedItem(
            key=item.key,
            object_type=item.object_type,
            operation="update",
            object_id=item.object_id,
            payload=item.changes,
            label=item.label,
            depends_on=item.depends_on,
        )
        for item in items
    ]


def _link(system_id: uuid.UUID, kind: str, id: uuid.UUID, table_id: uuid.UUID | None = None) -> str:
    """An app link, relative to the Workspace page: the system's Source Schema folder with
    the object named in the query (the app has no page of its own for an object yet)."""
    folder = f"?folder=systems/{system_id}/source-schema"
    if kind == "column":
        return f"{folder}&table={table_id}&column={id}"
    return f"{folder}&{kind}={id}"


WITHHELD = (
    "Withheld: view and routine definitions can embed literal values, so they are sent only "
    "when the Workspace shares samples."
)


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _protected_column(system_id: uuid.UUID, table_id: uuid.UUID, column: Any) -> dict[str, Any]:
    """A Protected Column as the model may see it: name and PII category, nothing else (not
    its type, comment, description or default, which can carry or hint at values)."""
    return {
        "id": column.id,
        "name": column.name,
        "is_protected": True,
        "pii_category": column.pii_category,
        "link": _link(system_id, "column", column.id, table_id),
    }


def _column_view(system_id: uuid.UUID, table_id: uuid.UUID, column: Any) -> dict[str, Any]:
    if column.is_protected:
        return _protected_column(system_id, table_id, column)
    return _without_none(
        {
            "id": column.id,
            "name": column.name,
            "status": column.status,
            "data_type": column.data_type,
            "is_nullable": column.is_nullable,
            "is_pk": column.is_pk,
            "default": column.default,
            "comment": column.comment,
            "description": column.description,
            "tags": column.tags or None,
            "is_protected": False,
            "link": _link(system_id, "column", column.id, table_id),
        }
    )


def _table_view(system_id: uuid.UUID, table: Any, definitions: bool) -> dict[str, Any]:
    return _without_none(
        {
            "id": table.id,
            "db_schema": table.db_schema,
            "name": table.name,
            "kind": table.kind,
            "status": table.status,
            "comment": table.comment,
            "description": table.description,
            "tags": table.tags or None,
            "classification": table.classification,
            "scd_hint": table.scd_hint,
            "row_estimate": table.row_estimate,
            "view_definition": table.view_definition if definitions else None,
            "definition_note": WITHHELD if table.view_definition and not definitions else None,
            "link": _link(system_id, "table", table.id),
            "columns": [_column_view(system_id, table.id, c) for c in table.columns],
            "constraints": [dataclasses.asdict(c) for c in table.constraints],
        }
    )


def _source_tables(ctx: ToolContext, system_id: uuid.UUID | None) -> tuple[uuid.UUID, Any]:
    if system_id is None:
        raise ApiError(422, "system_required", "Give the Source System's `system_id`.")
    schema = ctx.services.snapshots.source_schema(ctx.user, ctx.workspace_id, system_id)
    return system_id, schema.content


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def _get_object(ctx: ToolContext, args: _GetObject) -> Any:
    if args.kind == "kpi":
        return ctx.services.kpis.get(ctx.user, ctx.workspace_id, args.id)
    system_id, content = _source_tables(ctx, args.system_id)
    if args.kind == "table":
        for table in content.tables:
            if table.id == args.id:
                return _table_view(system_id, table, ctx.policy.allows(DataSharingLevel.SAMPLES))
        raise _not_found("Table")
    if args.kind == "routine":
        definitions = ctx.policy.allows(DataSharingLevel.SAMPLES)
        for routine in content.routines:
            if routine.id == args.id:
                return _without_none(
                    {
                        "id": routine.id,
                        "db_schema": routine.db_schema,
                        "name": routine.name,
                        "kind": routine.kind,
                        "status": routine.status,
                        "signature": routine.signature,
                        "definition": routine.definition if definitions else None,
                        "definition_note": WITHHELD
                        if routine.definition and not definitions
                        else None,
                        "link": _link(system_id, "routine", routine.id),
                    }
                )
        raise _not_found("Routine")
    for table in content.tables:
        for column in table.columns:
            if column.id == args.id:
                return {
                    **_column_view(system_id, table.id, column),
                    "table": table.name,
                    "db_schema": table.db_schema,
                    "table_link": _link(system_id, "table", table.id),
                }
    raise _not_found("Column")


def _search_catalog(ctx: ToolContext, args: _SearchCatalog) -> Any:
    snapshots = ctx.services.snapshots
    hits = snapshots.search(ctx.user, ctx.workspace_id, args.system_id, args.query, args.limit)
    protected: set[uuid.UUID] = set()
    tables: dict[tuple[str | None, str], uuid.UUID] = {}
    if any(hit.kind == "column" for hit in hits):
        content = snapshots.source_schema(ctx.user, ctx.workspace_id, args.system_id).content
        for table in content.tables:
            tables[(table.db_schema, table.name)] = table.id
            protected.update(c.id for c in table.columns if c.is_protected)
    results = []
    for hit in hits:
        table_id = tables.get((hit.db_schema, hit.table or "")) if hit.kind == "column" else None
        kind = "table" if hit.kind == "view" else hit.kind
        results.append(
            _without_none(
                {
                    "kind": hit.kind,
                    "id": hit.id,
                    "name": hit.name,
                    "db_schema": hit.db_schema,
                    "table": hit.table,
                    "status": hit.status,
                    "is_protected": True if hit.id in protected else None,
                    "link": _link(args.system_id, kind, hit.id, table_id)
                    if kind in ("table", "column", "routine")
                    else None,
                }
            )
        )
    return results


def _protected_categories(ctx: ToolContext, system_id: uuid.UUID) -> dict[uuid.UUID, Any]:
    _, content = _source_tables(ctx, system_id)
    return {c.id: c for t in content.tables for c in t.columns if c.is_protected}


def _get_profile(ctx: ToolContext, args: _GetProfile) -> Any:
    services = ctx.services
    table = services.profiling.table_profile(
        ctx.user, ctx.workspace_id, args.system_id, args.table_id
    )
    if args.column_id is not None and all(c.column_id != args.column_id for c in table.columns):
        raise _not_found("Column")
    protected = _protected_categories(ctx, args.system_id)
    values = ctx.policy.allows(DataSharingLevel.SAMPLES)
    columns: list[dict[str, Any]] = []
    for column in table.columns:
        if args.column_id is not None and column.column_id != args.column_id:
            continue
        if column.is_protected:
            found = protected.get(column.column_id)
            columns.append(
                {
                    "id": column.column_id,
                    "name": column.name,
                    "is_protected": True,
                    "pii_category": found.pii_category if found else None,
                    "link": _link(args.system_id, "column", column.column_id, table.table_id),
                }
            )
            continue
        profile = column.profile
        view: dict[str, Any] = {
            "id": column.column_id,
            "name": column.name,
            "data_type": column.data_type,
            "is_protected": False,
            "profiled": profile is not None,
            "link": _link(args.system_id, "column", column.column_id, table.table_id),
        }
        if profile is not None:
            view.update(
                row_count=profile.row_count,
                sampled=profile.sampled,
                null_pct=profile.null_pct,
                distinct_count=profile.distinct_count,
                avg_len=profile.avg_len,
                max_len=profile.max_len,
                patterns=profile.patterns,
                profiled_at=profile.profiled_at.isoformat(),
            )
            if values:  # min, max and the most frequent values are data, not statistics
                view.update(min=profile.min, max=profile.max, top_values=profile.top_values)
        columns.append(view)
    return {
        "table_id": table.table_id,
        "db_schema": table.db_schema,
        "name": table.name,
        "row_count": table.row_count,
        "profiled_at": table.profiled_at.isoformat() if table.profiled_at else None,
        "link": _link(args.system_id, "table", table.table_id),
        "columns": columns,
    }


def _get_snapshot_diff(ctx: ToolContext, args: _GetSnapshotDiff) -> Any:
    snapshots = ctx.services.snapshots
    ids = [s.id for s in snapshots.list(ctx.user, ctx.workspace_id, args.system_id)]  # newest first
    newer = args.snapshot_id or (ids[0] if ids else None)
    older = args.against_id
    if older is None and newer is not None:
        later = ids[ids.index(newer) + 1 :] if newer in ids else []
        older = later[0] if later else None
    if newer is None or older is None:
        raise ApiError(409, "no_diff", "A diff needs two Snapshots of the Source System.")
    result = dataclasses.asdict(
        snapshots.diff(ctx.user, ctx.workspace_id, args.system_id, newer, older)
    )
    # A column the newer Snapshot dropped is still protected: look in both Snapshots.
    protected = snapshots.protected_columns(
        ctx.user, ctx.workspace_id, args.system_id, [newer, older]
    )
    definitions = ctx.policy.allows(DataSharingLevel.SAMPLES)
    for table in result["tables"]:
        table["link"] = _link(args.system_id, "table", table["id"])
        _withhold_definitions(table["fields"], definitions)
        table["columns"] = [
            _diff_column(args.system_id, table["id"], column, protected)
            for column in table["columns"]
        ]
    for routine in result["routines"]:
        _withhold_definitions(routine["fields"], definitions)
    return result


def _diff_column(
    system_id: uuid.UUID, table_id: uuid.UUID, column: dict[str, Any], protected: set[uuid.UUID]
) -> dict[str, Any]:
    """A changed column; a Protected Column only by name and kind of change (its comment and
    default, before and after, can hold example values)."""
    link = _link(system_id, "column", column["id"], table_id)
    if column["id"] in protected:
        return {key: column[key] for key in ("id", "name", "change")} | {"link": link}
    return column | {"link": link}


def _withhold_definitions(fields: list[dict[str, Any]], shown: bool) -> None:
    if shown:
        return
    for change in fields:
        if change["field"] in ("view_definition", "definition"):
            change["before"] = change["after"] = None
            change["note"] = WITHHELD


def _get_pii_findings(ctx: ToolContext, args: _GetPiiFindings) -> Any:
    findings = ctx.services.pii.list_findings(
        ctx.user, ctx.workspace_id, args.system_id, status=args.status
    )
    return [
        {
            "id": f.id,
            "db_schema": f.db_schema,
            "table": f.table,
            "column": f.column,
            "category": f.category,
            "confidence": f.confidence,
            "status": f.status,
            "rule": f.rule,
            "is_protected": f.is_protected,
            "table_link": _link(args.system_id, "table", f.table_id),
            "link": _link(args.system_id, "column", f.column_id, f.table_id),
        }
        for f in findings
    ]


def _search_documents(ctx: ToolContext, args: _SearchDocuments) -> Any:
    passages = ctx.services.documents.search(
        ctx.user, ctx.workspace_id, args.query, system_id=args.system_id, limit=args.limit
    )
    return [
        {
            "file_id": p.file_id,
            "document": p.document,
            "section": p.section,
            "text": p.text,
            "source_system_id": p.source_system_id,
            "link": f"?folder=systems/{p.source_system_id}/documents&file={p.file_id}",
        }
        for p in passages
    ]


def _list_files(ctx: ToolContext, args: _ListFiles) -> Any:
    files = ctx.services.files
    if args.system_id is None:
        return files.list_for_warehouse(ctx.user, ctx.workspace_id).items
    return files.list_for_system(ctx.user, ctx.workspace_id, args.system_id).items


def _read_file(ctx: ToolContext, args: _ReadFile) -> Any:
    text = ctx.services.files.read_text(ctx.user, ctx.workspace_id, args.file_id)
    content = text.content
    if len(content) > MAX_FILE_TEXT_CHARS:
        content = content[:MAX_FILE_TEXT_CHARS] + "\n[truncated]"
    return {"name": text.file.name, "content": content}


def _generate_file(ctx: ToolContext, args: _GenerateFile) -> Any:
    # Model-written text may quote a value it saw: validator-detectable PII is redacted.
    return ctx.services.files.save_generated(
        ctx.user,
        ctx.workspace_id,
        args.system_id,
        name=redact_text(args.name),
        data=redact_text(args.content).encode(),
    )


def _run_source_query(ctx: ToolContext, args: _RunSourceQuery) -> Any:
    result = ctx.services.source_queries.run(
        ctx.user, ctx.workspace_id, args.system_id, args.sql, budget=ctx.budget
    )
    content: dict[str, Any] = {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "duration_ms": result.duration_ms,
        "source_query_seconds_left": round(ctx.budget.remaining),
    }
    if result.truncated:
        content["note"] = f"Only the first {result.row_count} rows are shown."
    if result.masked_columns:
        content["masked_columns"] = result.masked_columns
        content["masked_note"] = (
            "These columns hold personal data and are masked; count and join on them instead."
        )
    saved = {
        "columns": result.columns,
        "row_count": result.row_count,
        "duration_ms": result.duration_ms,
        "can_write": result.can_write,
    }
    return Reported(content, saved)


def _propose_changes(ctx: ToolContext, args: _ProposeChanges) -> Any:
    """The changes are not made: they become a Change Set the user reviews."""
    if ctx.proposals is not None:  # in a job: one Change Set when the job succeeds
        proposals = ctx.proposals
        proposals.check(args.source_system_id, args.items)
        # Validated now, so a bad object is reported to the model rather than failing the job.
        ctx.services.change_sets.validate(
            ctx.user,
            ctx.workspace_id,
            scope={
                "kind": "source_enhancements",
                "source_system_id": str(args.source_system_id),
            },
            items=_proposed_items(args.items),
            earlier_keys=[item.key for item in proposals.items],
        )
        total = proposals.add(args.source_system_id, args.items)
        return {
            "status": "collected",
            "note": "These changes join the job's Change Set, which the user reviews when the "
            "job finishes; nothing has been changed yet.",
            "items_so_far": total,
        }
    detail = ctx.services.change_sets.propose(
        ctx.user,
        ctx.workspace_id,
        origin="ai",
        scope={"kind": "source_enhancements", "source_system_id": str(args.source_system_id)},
        title=args.title,
        conversation_id=ctx.conversation_id,
        items=[
            ProposedItem(
                key=item.key,
                object_type=item.object_type,
                operation="update",
                object_id=item.object_id,
                payload=item.changes,
                label=item.label,
                depends_on=item.depends_on,
            )
            for item in args.items
        ],
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "note": "The user reviews these changes; nothing has been changed yet.",
        "items": len(detail.items),
    }


def _start_job(ctx: ToolContext, args: _StartJob) -> Any:
    job = ctx.services.jobs.submit(
        ctx.workspace_id,
        ASSISTANT_JOB,
        {
            "user_id": str(ctx.user.id),
            "workspace_id": str(ctx.workspace_id),
            "title": args.title,
            "conversation_id": str(ctx.conversation_id) if ctx.conversation_id else None,
            "task": args.task,
        },
        title=args.title,
        created_by=ctx.user.id,
    )
    content = {
        "job_id": job.id,
        "status": job.status,
        "note": "The job runs in the background and the member follows it in the chat. "
        "Tell them it started; do not wait for its result.",
    }
    return Reported(content, {"job_id": str(job.id), "title": job.title})


TOOLS: tuple[Tool, ...] = (
    Tool(
        "search_catalog",
        "Find tables, views, columns and routines of a Source System by part of their name.",
        _SearchCatalog,
        "read",
        Action.VIEW_WORKSPACE,
        _search_catalog,
    ),
    Tool(
        "get_object",
        "Read an object with its details: a KPI, or a Source System's table (with its "
        "columns), column or routine.",
        _GetObject,
        "read",
        Action.VIEW_WORKSPACE,
        _get_object,
    ),
    Tool(
        "get_profile",
        "Read a table's or column's profile statistics (null share, distinct count, lengths, "
        "patterns). Most frequent values, min and max come only when the Workspace shares "
        "samples. A Protected Column is given by name and PII category only.",
        _GetProfile,
        "read",
        Action.VIEW_WORKSPACE,
        _get_profile,
        level=DataSharingLevel.PROFILES,
    ),
    Tool(
        "get_snapshot_diff",
        "What changed between two Snapshots of a Source System (default: the latest and the "
        "one before).",
        _GetSnapshotDiff,
        "read",
        Action.VIEW_WORKSPACE,
        _get_snapshot_diff,
    ),
    Tool(
        "get_pii_findings",
        "List a Source System's PII findings: columns that look personal, with category and "
        "status. Never values.",
        _GetPiiFindings,
        "read",
        Action.REVIEW_PII,
        _get_pii_findings,
    ),
    Tool(
        "search_documents",
        "Search the uploaded documents; each passage names its document and section.",
        _SearchDocuments,
        "read",
        Action.VIEW_WORKSPACE,
        _search_documents,
        level=DataSharingLevel.DOCUMENTS,
    ),
    Tool(
        "list_files",
        "List the files in a Source System's file area or the Data Warehouse's.",
        _ListFiles,
        "read",
        Action.VIEW_WORKSPACE,
        _list_files,
    ),
    Tool(
        "read_file",
        "Read a text file or document from a file area.",
        _ReadFile,
        "read",
        Action.VIEW_WORKSPACE,
        _read_file,
        level=DataSharingLevel.DOCUMENTS,
    ),
    Tool(
        "run_source_query",
        "Run one read-only SELECT on a Source System that has a live Connection, to check a "
        "hunch (e.g. a COUNT(*) overlap between two columns). At most 100 rows come back, "
        "values of personal-data columns are masked, and each run draws on a time budget.",
        _RunSourceQuery,
        "read",
        Action.RUN_SOURCE_QUERY,
        _run_source_query,
        level=DataSharingLevel.SAMPLES,
    ),
    Tool(
        "generate_file",
        "Save a new text file in a Source System's file area (overwrites one of the same name).",
        _GenerateFile,
        "write",
        Action.UPLOAD_FILE,
        _generate_file,
    ),
    Tool(
        "propose_changes",
        "Propose descriptions, tags, sensitivity flags, classifications and SCD hints for "
        "a Source System's tables and columns. Nothing changes until the user accepts the "
        "proposal as a Change Set.",
        _ProposeChanges,
        "write",
        Action.REVIEW_CHANGE_SETS,
        _propose_changes,
    ),
    Tool(
        "start_job",
        "Hand a large task (exploring many tables, generating a whole layer) to a background "
        "job that can make many more tool calls than this chat. It reports progress here and "
        "ends in one Change Set for the user to review. Give the complete task.",
        _StartJob,
        "write",
        Action.ASK_ASSISTANT,
        _start_job,
        chat_only=True,
    ),
)


def _json(value: Any) -> str:
    """The result as JSON of at most ``MAX_RESULT_CHARS``: too long, the longest lists lose
    their tail (the list's owner says ``truncated: true``), so it is always valid JSON."""

    def plain(item: Any) -> Any:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return dataclasses.asdict(item)
        return str(item)

    def dump(data: Any) -> str:
        return json.dumps(data, default=plain, ensure_ascii=False)

    text = dump(value)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    data = json.loads(text)
    if isinstance(data, list):
        data = {"items": data}
    while len(text) > MAX_RESULT_CHARS:
        if not _shrink(data):
            return dump({"truncated": True, "note": "The result is too large to show."})
        text = dump(data)
    return text


def _shrink(data: Any) -> bool:
    """Halve the biggest list, or cut the longest string; ``False`` if nothing can shrink."""
    lists: list[tuple[Any, Any]] = []  # (holder, key) of every list with more than one item
    strings: list[tuple[Any, Any]] = []
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        pairs = node.items() if isinstance(node, dict) else enumerate(node)
        for key, child in list(pairs):
            if isinstance(child, list | dict):
                stack.append(child)
                if isinstance(child, list) and len(child) > 1:
                    lists.append((node, key))
            elif isinstance(child, str) and len(child) > 1_000:
                strings.append((node, key))
    if lists:
        holder, key = max(lists, key=lambda p: len(json.dumps(p[0][p[1]], default=str)))
        holder[key] = holder[key][: len(holder[key]) // 2]
        if isinstance(holder, dict):
            holder["truncated"] = True
        return True
    if strings:
        holder, key = max(strings, key=lambda p: len(p[0][p[1]]))
        holder[key] = holder[key][: len(holder[key]) // 2] + " [truncated]"
        return True
    return False


class ToolRegistry:
    """The tools of one installation, ready to be bound to a user in a Workspace."""

    def __init__(
        self,
        workspaces: WorkspaceService,
        services: ToolServices,
        tools: Sequence[Tool] = TOOLS,
        *,
        source_query_seconds: float = DEFAULT_BUDGET_SECONDS,
    ) -> None:
        self._workspaces = workspaces
        self._services = services
        self._source_query_seconds = source_query_seconds
        self._tools = {tool.name: tool for tool in tools}

    def bind(
        self,
        user: User,
        workspace_id: uuid.UUID,
        policy: DataSharingPolicy,
        conversation_id: uuid.UUID | None = None,
        *,
        job: bool = False,
    ) -> BoundTools:
        """The tools of one run. ``job``: a background job's tools (no ``start_job``; its
        proposed changes are held for one Change Set, see ``BoundTools.submit_proposals``)."""
        ctx = ToolContext(
            user,
            workspace_id,
            self._services,
            policy,
            conversation_id,
            QueryBudget(self._source_query_seconds),
            Proposals() if job else None,
        )
        return BoundTools(self, ctx, policy)

    def allowed(self, ctx: ToolContext, policy: DataSharingPolicy, tool: Tool) -> ApiError | None:
        """Why ``tool`` may not be used now, or ``None``."""
        if tool.level is not None and not policy.allows(tool.level):
            return ApiError(
                403,
                "data_sharing_level",
                f"The Workspace's data-sharing level does not allow `{tool.name}`: an owner "
                f"must raise it to `{tool.level.value}`.",
            )
        try:
            self._workspaces.authorize(ctx.user, tool.action, ctx.workspace_id)
        except ApiError as exc:
            return exc
        return None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())


class BoundTools:
    """The ``ToolSet`` of one run: the registry as seen by one user in one Workspace."""

    def __init__(self, registry: ToolRegistry, ctx: ToolContext, policy: DataSharingPolicy) -> None:
        self._registry = registry
        self._ctx = ctx
        self._policy = policy

    def specs(self) -> list[ToolSpec]:
        """Only the tools this user may use now; execution checks again regardless."""
        return [
            tool.spec
            for tool in self._registry.all()
            if self._offered(tool) and self._registry.allowed(self._ctx, self._policy, tool) is None
        ]

    def _offered(self, tool: Tool) -> bool:
        return not (tool.chat_only and self._ctx.proposals is not None)

    def submit_proposals(self, title: str) -> int:
        """Make the changes a job collected into one Change Set; how many items it has (0:
        none were proposed, so there is no Change Set)."""
        proposals = self._ctx.proposals
        if proposals is None or proposals.source_system_id is None:
            return 0
        detail = self._ctx.services.change_sets.propose(
            self._ctx.user,
            self._ctx.workspace_id,
            origin="ai",
            scope={
                "kind": "source_enhancements",
                "source_system_id": str(proposals.source_system_id),
            },
            title=title,
            conversation_id=self._ctx.conversation_id,
            items=[
                ProposedItem(
                    key=item.key,
                    object_type=item.object_type,
                    operation="update",
                    object_id=item.object_id,
                    payload=item.changes,
                    label=item.label,
                    depends_on=item.depends_on,
                )
                for item in proposals.items
            ],
        )
        return len(detail.items)

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        tool = self._registry.get(name)
        if tool is None or not self._offered(tool):
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        refusal = self._registry.allowed(self._ctx, self._policy, tool)
        if refusal is not None:
            return ToolOutcome(f"Refused: {refusal.message}", "refused")
        try:
            args = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return ToolOutcome(f"Invalid arguments for `{name}`: {problems}", "error")
        try:
            result = tool.run(self._ctx, args)
            if isinstance(result, Reported):
                return ToolOutcome(_json(result.content), "ok", result.saved)
            return ToolOutcome(_json(result), "ok")
        except ApiError as exc:
            status = "refused" if exc.status_code in (401, 403) else "error"
            return ToolOutcome(f"{exc.code}: {exc.message}", status)
        except Exception:
            logger.exception("assistant tool failed", extra={"tool": name})
            return ToolOutcome(f"The `{name}` tool failed.", "error")
