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
import difflib
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from dawam.modules.auth import User
from dawam.modules.changesets import ChangeSetService, ProposedItem
from dawam.modules.files import DocumentSearchService, FileService, PassageFilters
from dawam.modules.jobs import PRIORITY_CHAT, JobService
from dawam.modules.kpis import KpiService, KpiSuggestion
from dawam.modules.lineage import LineageService
from dawam.modules.llm import DataSharingLevel, DataSharingPolicy, ToolSpec
from dawam.modules.sources import (
    DEFAULT_BUDGET_SECONDS,
    ConnectionService,
    DataDictionaryService,
    PiiService,
    ProfilingService,
    QueryBudget,
    RelationshipService,
    SnapshotService,
    SourceQueryService,
    SourceSystemService,
)
from dawam.modules.warehouse import (
    DdlService,
    DraftBranch,
    DraftColumn,
    DraftNote,
    DraftTable,
    EvaluationService,
    Finding,
    FindingItem,
    GeneratedColumn,
    GeneratedTable,
    GenerationPlan,
    GenerationService,
    MappingDraftPlan,
    MappingDraftService,
    MappingExportService,
    ModelService,
    ScoreService,
    ValidationService,
)
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.pagination import MAX_PAGE_SIZE
from dawam.platform.pii_validators import STRUCTURED_KEYS, redact_text

from . import diagrams
from .agent import ToolOutcome

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 20_000
MAX_FILE_TEXT_CHARS = 15_000
MAX_PROPOSED_ITEMS = 200
MAX_JOB_ITEMS = 1000
MAX_DRAFT_INPUTS = 10
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
    scores: ScoreService
    evaluations: EvaluationService
    lineage: LineageService
    validation: ValidationService
    source_queries: SourceQueryService
    jobs: JobService
    generation: GenerationService
    relationships: RelationshipService
    mapping_drafts: MappingDraftService
    ddl: DdlService
    mapping_exports: MappingExportService
    dictionaries: DataDictionaryService
    systems: SourceSystemService
    connections: ConnectionService
    model: ModelService


@dataclass(frozen=True)
class CountEvidence:
    """What a run keeps of a source query that returned counts: the counts, never a row."""

    system_id: uuid.UUID
    sql: str
    """The model's SQL, validator-detectable PII redacted."""
    counts: list[dict[str, Any]]
    """One ``{"column": name, "count": n}`` per COUNT column (names can repeat)."""


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
    counts: dict[str, CountEvidence] = field(default_factory=dict)
    """The run's count queries by ``query_ref``, citable as relationship evidence. A ref is
    unique, so one from an earlier message is refused rather than mistaken for another."""


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
    compact: bool = False
    """Drop the schema's generated titles: the tool's schema is large and sent on every call."""

    @property
    def spec(self) -> ToolSpec:
        schema = self.arguments.model_json_schema()
        schema.pop("title", None)
        if self.compact:
            _drop_titles(schema)
        return ToolSpec(self.name, self.description, schema)


SYSTEM_ID = "The Source System's id."


def _drop_titles(node: Any) -> None:
    """Remove the ``title`` keyword pydantic adds to every schema, not fields named ``title``."""
    if not isinstance(node, dict):
        return
    node.pop("title", None)
    for key in ("properties", "$defs"):
        for sub in node.get(key, {}).values():
            _drop_titles(sub)
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in node.get(key, []):
            _drop_titles(sub)
    for key in ("items", "additionalProperties"):
        _drop_titles(node.get(key))


class _SearchCatalog(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    query: str = Field(min_length=1, max_length=200, description="Part of an object's name.")
    limit: int = Field(default=20, ge=1, le=50)


SearchKind = Literal["source", "dw", "kpi", "document"]


class _SearchWorkspace(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    kinds: list[SearchKind] | None = Field(
        default=None,
        description="Where to look: source (Source System tables, views, columns, routines), "
        "dw (Data Warehouse tables and columns), kpi, document. Leave out to search all.",
    )
    limit: int = Field(default=5, ge=1, le=20, description="At most this many per kind.")


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


class _GetLineage(BaseModel):
    node_id: uuid.UUID = Field(
        description="A source column, DW column, DW table or KPI id to start from."
    )
    direction: Literal["upstream", "downstream", "both"] = Field(
        default="upstream",
        description="upstream: where it comes from; downstream: what depends on it.",
    )
    depth: int = Field(default=64, ge=1, le=64, description="At most this many edges away.")


LINEAGE_NODE_LIMIT = 200
"""The most nodes a ``get_lineage`` result names; the rest are counted."""


class _ExploreGraph(BaseModel):
    object_id: uuid.UUID = Field(
        description="A source column, DW column, DW table or KPI id to start from."
    )
    depth: int = Field(default=1, ge=1, le=2, description="At most this many hops away (1 or 2).")
    system_id: uuid.UUID | None = Field(
        default=None,
        description="The Source System's id, to include its source columns' relationships.",
    )


GRAPH_NODE_LIMIT = 100
"""The most nodes an ``explore_graph`` result names; the rest are counted."""
GRAPH_DOCUMENT_LIMIT = 5
GRAPH_PASSAGE_CHARS = 500


class _GetScore(BaseModel):
    limit: int = Field(
        default=30, ge=1, le=100, description="At most this many failed checks, errors first."
    )


class _ExplainCheck(BaseModel):
    check_code: str = Field(min_length=1, max_length=100, description="The failed check's code.")
    object_id: uuid.UUID = Field(description="The table or column the check failed on.")


class _FindingChange(BaseModel):
    object_type: str = Field(description="A Change Set object type, e.g. `kpi`.")
    operation: Literal["create", "update", "delete"]
    object_id: uuid.UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    label: str = Field(default="", max_length=200)


class _EvaluationFinding(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=2000, description="What is wrong and why.")
    category: Literal[
        "ambiguous_grain",
        "scd2_candidate",
        "missing_conformed_dimension",
        "uncomputable_kpi",
        "other",
    ] = "other"
    severity: Literal["high", "medium", "low"] = "medium"
    table_id: uuid.UUID | None = Field(default=None, description="The table at fault, if any.")
    suggestion: str = Field(default="", max_length=2000)
    changes: list[_FindingChange] = Field(
        default_factory=list,
        max_length=50,
        description="Concrete Change Set items, only when you can name them exactly.",
    )


class _ProposeCheckFix(_ExplainCheck):
    title: str = Field(min_length=1, max_length=200, description="What the fix does.")
    changes: list[_FindingChange] = Field(
        min_length=1, max_length=50, description="The exact Change Set items that fix the check."
    )


class _EvaluateDw(BaseModel):
    layer: Literal["staging", "core", "mart"]
    findings: list[_EvaluationFinding] | None = Field(
        default=None,
        max_length=50,
        description="Leave out to read the Layer; then call again with your findings "
        "(an empty list if there are none) to store the evaluation.",
    )


class _SearchDocuments(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    system_id: uuid.UUID | None = Field(
        default=None, description="Only this Source System's documents."
    )
    document_type: str | None = Field(default=None, description="Only this type, e.g. pdf, xlsx.")
    language: str | None = Field(default=None, description="Only ar or en passages.")
    page: int | None = Field(default=None, ge=1, description="Only this PDF page.")
    sheet: str | None = Field(default=None, description="Only this worksheet.")
    tag: str | None = Field(default=None, description="Only documents in this folder.")
    limit: int = Field(default=5, ge=1, le=20)


class _DescribeImage(BaseModel):
    file_id: uuid.UUID


class _ListFiles(BaseModel):
    system_id: uuid.UUID | None = Field(
        default=None,
        description="A Source System's id; leave out for the Data Warehouse's file area.",
    )


class _WorkspaceOverview(BaseModel):
    pass


def _workspace_overview(ctx: ToolContext, args: _WorkspaceOverview) -> Any:
    """Everything the member can see, with ids: the ids other tools need."""
    services = ctx.services
    base = f"/workspaces/{ctx.workspace_id}"
    connections = {
        c.system_id: c for c in services.connections.statuses(ctx.user, ctx.workspace_id)
    }
    systems = []
    cursor = None
    while True:
        page = services.systems.list(ctx.user, ctx.workspace_id, limit=MAX_PAGE_SIZE, cursor=cursor)
        for system in page.items:
            connection = connections.get(system.id)
            snapshots = services.snapshots.list(ctx.user, ctx.workspace_id, system.id)
            files = services.files.list_for_system(ctx.user, ctx.workspace_id, system.id)
            systems.append(
                {
                    "id": system.id,
                    "name": system.name,
                    "code": system.code,
                    "engine": connection.engine if connection else None,
                    "connection": connection.state if connection else "not_connected",
                    "snapshot": {
                        "count": len(snapshots),
                        "latest_taken_at": snapshots[0].taken_at.isoformat(),
                        "table_count": snapshots[0].table_count,
                    }
                    if snapshots
                    else None,
                    "file_count": len(files.items),
                    "files_truncated": files.next_cursor is not None or None,
                    "link": f"{base}?folder=systems/{system.id}",
                }
            )
        cursor = page.next_cursor
        if cursor is None:
            break
    layers: dict[str, list[Any]] = {"staging": [], "core": [], "mart": []}
    for table in services.model.list_tables(ctx.user, ctx.workspace_id):
        layers[table.layer].append({"id": table.id, "name": table.name, "kind": table.kind})
    kpi_count = 0
    cursor = None
    while True:
        kpi_page = services.kpis.list(
            ctx.user, ctx.workspace_id, limit=MAX_PAGE_SIZE, cursor=cursor
        )
        kpi_count += len(kpi_page.items)
        cursor = kpi_page.next_cursor
        if cursor is None:
            break
    try:
        warehouse_files = len(services.files.list_for_warehouse(ctx.user, ctx.workspace_id).items)
    except ApiError as exc:  # no Data Warehouse yet, so no file area
        if exc.status_code != 404:
            raise
        warehouse_files = 0
    return {
        "source_systems": [_without_none(s) for s in systems],
        "dw_layers": [
            {
                "layer": name,
                "table_count": len(tables),
                "tables": tables,
                "link": f"{base}/data-warehouse",
            }
            for name, tables in layers.items()
        ],
        "kpi_count": kpi_count,
        "data_warehouse_file_count": warehouse_files,
        "link": base,
    }


class _ReadFile(BaseModel):
    file_id: uuid.UUID


FileKind = Literal["text", "ddl", "mapping_sheet", "data_dictionary"]


class _GenerateFile(BaseModel):
    kind: FileKind = Field(
        default="text",
        description="`text` saves the `content` you write (Markdown, SQL, CSV...); `ddl` saves "
        "the Data Warehouse's DDL package, `mapping_sheet` its Core and Mart mapping sheet "
        "(XLSX) and `data_dictionary` a Source System's data dictionary (XLSX), all produced by "
        "the exporters.",
    )
    system_id: uuid.UUID | None = Field(
        default=None,
        description="The Source System whose file area gets the file; leave out for the Data "
        "Warehouse's file area (always so for `ddl` and `mapping_sheet`, never for "
        "`data_dictionary`).",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="File name, e.g. `notes.md`; needed for `text`, an exporter has its own.",
    )
    content: str = Field(
        default="", max_length=200_000, description="The file's text; only for `text`."
    )
    layer: Literal["staging", "core", "mart"] | None = Field(
        default=None, description="Limit `ddl` or `mapping_sheet` to one Layer."
    )
    replace: bool = Field(
        default=False,
        description="Set only after the user has seen the diff of an existing file and agreed "
        "to overwrite it.",
    )


class _UpdateFile(BaseModel):
    file_id: uuid.UUID = Field(description="The text file's id, from list_files.")
    content: str = Field(
        max_length=200_000, description="The file's complete new text, not just the change."
    )
    title: str = Field(
        default="Update file", min_length=1, max_length=200, description="A short title."
    )


class _KpiIdea(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="The KPI's name.")
    rationale: str = Field(
        min_length=1,
        max_length=4000,
        description="Why this KPI fits: which tables, columns or documents point to it.",
    )
    definition: str = Field(default="", max_length=4000, description="The business definition.")
    formula_text: str = Field(default="", max_length=4000, description="The formula in words.")
    unit: str = Field(default="", max_length=50)
    aggregation: str = Field(default="", max_length=50)


class _SuggestKpis(BaseModel):
    suggestions: list[_KpiIdea] = Field(min_length=1, max_length=20)
    system_id: uuid.UUID | None = Field(
        default=None,
        description="The Source System the KPIs are documented under; leave out for the "
        "Data Warehouse.",
    )


class _RunSourceQuery(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    sql: str = Field(
        min_length=1,
        max_length=4000,
        description="One SELECT over the Source System's tables, e.g. a COUNT(*) overlap check. "
        "Name tables as schema.table. Protected columns can be counted and joined on, not shown.",
    )


class _ChangeItem(BaseModel):
    key: str = Field(min_length=1, max_length=40, description="Short name, for `depends_on`.")
    object_type: Literal["source_table", "source_column"] = Field(
        description="`source_table` or `source_column`."
    )
    object_id: uuid.UUID = Field(description="The table's or column's id.")
    changes: dict[str, Any] = Field(
        min_length=1,
        description="Field to new value: `description` (text), `tags` (list of text), "
        "`is_sensitive` (boolean); tables also `classification` (master, transactional, "
        "reference, log, landing) and `scd_hint` (text).",
    )
    label: str = Field(default="", max_length=200, description="E.g. `core.customers`.")
    depends_on: list[str] = Field(default_factory=list, description="Keys of earlier items.")


class _RelationshipDecision(BaseModel):
    relationship_id: uuid.UUID = Field(description="From get_relationships.")
    decision: Literal["accept", "reject"]
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description="Why: the names, profiles, documents or counts that support it.",
    )
    evidence_queries: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="`query_ref`s of run_source_query COUNTs that support it.",
    )
    label: str = Field(
        default="",
        max_length=200,
        description="E.g. `a.b.col -> c.d.id`.",
    )


class _ProposeChanges(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="What the changes are for.")
    source_system_id: uuid.UUID = Field(description="The Source System the objects belong to.")
    items: list[_ChangeItem] = Field(default_factory=list, max_length=MAX_PROPOSED_ITEMS)
    relationships: list[_RelationshipDecision] = Field(
        default_factory=list,
        max_length=MAX_PROPOSED_ITEMS,
        description="Accept or reject inferred relationships.",
    )


class _GetRelationships(BaseModel):
    system_id: uuid.UUID = Field(description=SYSTEM_ID)
    status: Literal["suggested", "accepted", "rejected"] | None = Field(
        default="suggested", description="Only relationships in this state; null for all."
    )


class _GetKpiLinkCandidates(BaseModel):
    pass


class _KpiLinkItem(BaseModel):
    kpi_id: uuid.UUID = Field(description="The KPI's id.")
    formula_sql: str | None = Field(
        default=None,
        max_length=20_000,
        description="One SELECT query against the DW Schema's Core and Mart tables; leave out "
        "to keep the KPI's own.",
    )
    dw_column_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=100,
        description="Ids of the DW columns the KPI uses, at the highest Layer that holds the "
        "measure (Mart if present, otherwise Core); leave out to keep the KPI's own links.",
    )
    label: str = Field(default="", max_length=200, description="The KPI's name.")


class _ProposeKpiLinks(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="What the proposal is for.")
    items: list[_KpiLinkItem] = Field(min_length=1, max_length=MAX_PROPOSED_ITEMS)


def _get_kpi_link_candidates(ctx: ToolContext, args: _GetKpiLinkCandidates) -> Any:
    found = ctx.services.kpis.link_candidates(ctx.user, ctx.workspace_id)
    schema = found.schema
    return {
        "kpis": [
            {
                "id": k.id,
                "name": k.name,
                "definition": k.definition,
                "formula_text": k.formula_text,
                "formula_sql": k.formula_sql,
                "unit": k.unit,
                "aggregation": k.aggregation,
            }
            for k in found.kpis
        ],
        "dw_schema": None
        if schema is None
        else {
            "platform": schema.platform,
            "layer_schemas": schema.layer_schemas,
            "tables": [
                {
                    "layer": t.layer,
                    "name": t.name,
                    "kind": t.kind,
                    "columns": [
                        {"id": c.id, "name": c.name, "role": c.role, "additivity": c.additivity}
                        for c in t.columns
                    ],
                }
                for t in schema.tables
            ],
        },
        "note": "KPIs here have no links or no formula SQL. Without a dw_schema the Data "
        "Warehouse is not set up and nothing can be proposed.",
    }


def _propose_kpi_links(ctx: ToolContext, args: _ProposeKpiLinks) -> Any:
    """The changes are not made: they become a Change Set the user reviews."""
    items = []
    for n, item in enumerate(args.items):
        payload: dict[str, Any] = {}
        if item.formula_sql is not None:
            payload["formula_sql"] = item.formula_sql
        if item.dw_column_ids is not None:
            payload["links"] = [str(i) for i in item.dw_column_ids]
        items.append(
            ProposedItem(
                key=f"kpi{n}",
                object_type="kpi",
                operation="update",
                object_id=item.kpi_id,
                payload=payload,
                label=item.label,
            )
        )
    detail = ctx.services.change_sets.propose(
        ctx.user,
        ctx.workspace_id,
        origin="ai",
        scope={"kind": "kpi_links"},
        title=args.title,
        conversation_id=ctx.conversation_id,
        items=items,
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "note": "The user reviews these changes; nothing has been changed yet.",
        "items": len(detail.items),
    }


class _RenderDiagram(BaseModel):
    kind: Literal["star_schema", "lineage"] = Field(
        description="`star_schema`: the DW model's fact tables and their dimensions; "
        "`lineage`: the lineage graph around a node."
    )
    scope: str | None = Field(
        default=None,
        max_length=200,
        description="star_schema: a Layer (core, mart), a table name or id; leave out for every "
        "fact table. lineage: the node's id (source column, DW column or table, KPI), needed.",
    )
    direction: Literal["upstream", "downstream", "both"] = Field(
        default="upstream", description="lineage only."
    )


def _render_diagram(ctx: ToolContext, args: _RenderDiagram) -> Any:
    """The diagram is built from the DW model or the lineage graph, not from the model."""
    if args.kind == "lineage":
        try:
            node_id = uuid.UUID(args.scope or "")
        except ValueError:
            raise ApiError(422, "scope_required", "A lineage diagram needs a node id.") from None
        graph = ctx.services.lineage.graph(ctx.user, ctx.workspace_id, node_id, args.direction, 64)
        source, left_out = diagrams.lineage(graph)
    else:
        schema = ctx.services.kpis.link_candidates(ctx.user, ctx.workspace_id).schema
        if schema is None:
            raise ApiError(409, "no_data_warehouse", "The Data Warehouse is not set up yet.")
        source, left_out = diagrams.star_schema(schema.tables, args.scope)
        if not source:
            raise _not_found("A fact table for that scope")
    return {
        "mermaid": source,
        "tables_or_nodes_left_out": left_out,
        "note": "Show the user this diagram by putting `mermaid` unchanged in a ```mermaid "
        "code block in your answer, then explain it briefly.",
    }


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
        self.items: list[ProposedItem] = []
        self.generation: GenerationPlan | None = None
        """The DW Schema generation's items; a later generation replaces an earlier one."""
        self.mapping_items: list[ProposedItem] = []
        self.mapping_tables: set[uuid.UUID] = set()
        self.mapping_notes: list[DraftNote] = []
        """The mapping drafts' items; a later draft of a table replaces an earlier one."""

    def add(self, source_system_id: uuid.UUID, items: Sequence[ProposedItem]) -> int:
        self.check(source_system_id, items)
        self.source_system_id = source_system_id
        self.items.extend(items)
        return len(self.items)

    def set_generation(self, plan: GenerationPlan) -> None:
        if self.items or self.mapping_tables:
            raise _one_change_set()
        self.generation = plan

    def add_mapping_draft(self, plan: MappingDraftPlan) -> int:
        """Collect a mapping draft; the items of a table drafted before are replaced.
        Returns the items so far."""
        if self.items or self.generation is not None:
            raise _one_change_set()
        tables = {str(i) for i in plan.table_ids}
        kept = [i for i in self.mapping_items if i.payload["table_id"] not in tables]
        if len(kept) + len(plan.items) > MAX_JOB_ITEMS:
            raise ApiError(422, "too_many_items", "This job has proposed too many changes.")
        self.mapping_items = [*kept, *plan.items]
        self.mapping_tables |= set(plan.table_ids)
        self.mapping_notes.extend(plan.notes)
        return len(self.mapping_items)

    def mapping_draft(self) -> MappingDraftPlan:
        items = [
            dataclasses.replace(item, key=f"map{n}") for n, item in enumerate(self.mapping_items)
        ]
        return MappingDraftPlan(items, sorted(self.mapping_tables), self.mapping_notes)

    def check(self, source_system_id: uuid.UUID, items: Sequence[ProposedItem]) -> None:
        if self.generation is not None or self.mapping_tables:
            raise _one_change_set()
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


def _one_change_set() -> ApiError:
    return ApiError(
        422,
        "one_change_set",
        "A job's changes go into one Change Set, so a job either generates the DW Schema, "
        "drafts mappings or proposes Source Schema changes, not a mix.",
    )


def new_query_ref() -> str:
    """A ``query_ref`` no other run uses."""
    return f"q{uuid.uuid4().hex[:8]}"


def _dotted(ref: Any) -> str:
    return f"{ref.db_schema}.{ref.table}.{ref.column}"


def _redacted(value: Any, *, entities: bool = True) -> Any:
    """Model-written text with validator-detectable PII redacted, in strings, lists and
    objects alike (spec §6.8: every model-written string is checked before it is saved).
    Identifiers and SQL (``STRUCTURED_KEYS``) skip NER-based masking of names."""
    if isinstance(value, str):
        return redact_text(value, entities=entities)
    if isinstance(value, list):
        return [_redacted(v, entities=entities) for v in value]
    if isinstance(value, dict):
        return {
            key: _redacted(v, entities=entities and key not in STRUCTURED_KEYS)
            for key, v in value.items()
        }
    return value


def _proposed_items(ctx: ToolContext, args: _ProposeChanges) -> list[ProposedItem]:
    """The items of a ``propose_changes`` call, model-written text redacted; a relationship
    decision carries the counts of the queries it cites. 422 for a ``query_ref`` this run
    did not make on this Source System, or that returned no counts."""
    if not args.items and not args.relationships:
        raise ApiError(422, "nothing_proposed", "Give at least one item or relationship.")
    labels: dict[uuid.UUID, str] = {}
    if any(not d.label for d in args.relationships):  # the reviewer sees which pair it is
        labels = {
            r.id: f"{_dotted(r.from_column)} -> {_dotted(r.to_column)}"
            for r in ctx.services.relationships.list_relationships(
                ctx.user, ctx.workspace_id, args.source_system_id, min_confidence=0
            )
        }
    proposed = [
        ProposedItem(
            key=item.key,
            object_type=item.object_type,
            operation="update",
            object_id=item.object_id,
            payload=_redacted(item.changes),
            label=redact_text(item.label),
            depends_on=item.depends_on,
        )
        for item in args.items
    ]
    for decision in args.relationships:
        queries = []
        for ref in decision.evidence_queries:
            found = ctx.counts.get(ref)
            if found is None or found.system_id != args.source_system_id:
                raise ApiError(
                    422,
                    "unknown_query_ref",
                    f"No count query `{ref}` of this Source System while answering this "
                    "message: run the COUNT query again and cite the `query_ref` it returns.",
                )
            queries.append({"sql": found.sql, "counts": found.counts})
        evidence: dict[str, Any] = {"reason": redact_text(decision.reason)}
        if queries:
            evidence["queries"] = queries
        proposed.append(
            ProposedItem(
                key=f"relationship:{decision.relationship_id}",
                object_type="source_relationship",
                operation="update",
                object_id=decision.relationship_id,
                payload={
                    "status": "accepted" if decision.decision == "accept" else "rejected",
                    "ai_evidence": evidence,
                },
                label=redact_text(decision.label)
                if decision.label  # the fallback is column paths, not prose
                else redact_text(labels.get(decision.relationship_id, ""), entities=False),
            )
        )
    return proposed


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


def _get_relationships(ctx: ToolContext, args: _GetRelationships) -> Any:
    found = ctx.services.relationships.list_relationships(
        ctx.user, ctx.workspace_id, args.system_id, status=args.status
    )

    def column(ref: Any) -> dict[str, Any]:
        return {
            "id": ref.column_id,
            "name": f"{ref.db_schema}.{ref.table}.{ref.column}",
            "link": _link(args.system_id, "column", ref.column_id, ref.table_id),
        }

    return {
        "relationships": [
            {
                "id": r.id,
                "from": column(r.from_column),
                "to": column(r.to_column),
                "origin": r.origin,
                "confidence": r.confidence,
                "status": r.status,
                "evidence": r.evidence,  # names and ratios, never a value
            }
            for r in found
        ],
        "note": "Confirm or refute a suggested relationship with what the data-sharing level "
        "allows (names, profiles, documents, or COUNT queries with run_source_query), then "
        "accept or reject it in propose_changes `relationships`.",
    }


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


def _get_lineage(ctx: ToolContext, args: _GetLineage) -> Any:
    graph = ctx.services.lineage.graph(
        ctx.user, ctx.workspace_id, args.node_id, args.direction, args.depth
    )
    nodes = graph.nodes[:LINEAGE_NODE_LIMIT]
    named = {n.id: f"{n.label} ({n.layer})" for n in nodes}
    return {
        "start": named.get(graph.start.id, graph.start.label),
        "nodes": [{"id": n.id, "type": n.type, "label": n.label, "layer": n.layer} for n in nodes],
        "edges": [
            {"kind": e.kind, "from": named[e.from_id], "to": named[e.to_id]}
            for e in graph.edges
            if e.from_id in named and e.to_id in named
        ],
        "nodes_left_out": len(graph.nodes) - len(nodes),
    }


def _explore_graph(ctx: ToolContext, args: _ExploreGraph) -> Any:
    """A breadth-first walk where one hop is a lineage step (KPI links included) or an
    accepted or suggested relationship; each service call authorizes as the member."""
    lineage = ctx.services.lineage
    around: dict[uuid.UUID, Any] = {}

    def near(node_id: uuid.UUID) -> Any:
        if node_id not in around:
            around[node_id] = lineage.graph(ctx.user, ctx.workspace_id, node_id, "both", 1)
        return around[node_id]

    related: dict[uuid.UUID, list[Any]] = {}
    if args.system_id is not None:
        for r in ctx.services.relationships.list_relationships(
            ctx.user, ctx.workspace_id, args.system_id
        ):
            if r.status != "rejected":
                related.setdefault(r.from_column.column_id, []).append(r)
                related.setdefault(r.to_column.column_id, []).append(r)

    start = near(args.object_id).start
    nodes: dict[uuid.UUID, tuple[Any, int]] = {start.id: (start, 0)}
    left_out: set[uuid.UUID] = set()
    edges: dict[tuple[str, uuid.UUID, uuid.UUID], dict[str, Any]] = {}

    def reach(node: Any, hop: int) -> bool:
        if node.id in nodes:
            return False
        if len(nodes) >= GRAPH_NODE_LIMIT:
            left_out.add(node.id)
            return False
        nodes[node.id] = (node, hop)
        return True

    frontier = [start.id]
    for hop in range(1, args.depth + 1):
        reached = []
        for node_id in frontier:
            if len(nodes) >= GRAPH_NODE_LIMIT:
                break
            step = near(node_id)
            for e in step.edges:
                edges.setdefault((e.kind, e.from_id, e.to_id), {"kind": e.kind})
            reached += [n.id for n in step.nodes if reach(n, hop)]
            for r in related.get(node_id, []):
                ends = (r.from_column.column_id, r.to_column.column_id)
                other = ends[1] if ends[0] == node_id else ends[0]
                if other not in nodes and len(nodes) >= GRAPH_NODE_LIMIT:
                    left_out.add(other)
                    continue
                if reach(near(other).start, hop):
                    reached.append(other)
                edges.setdefault(
                    ("relationship", *ends),
                    {"kind": "relationship", "status": r.status, "confidence": r.confidence},
                )
        frontier = reached

    named = {i: f"{n.label} ({n.layer})" for i, (n, _) in nodes.items()}
    note = (
        "Edge kinds: value, uses and lookup (lineage), kpi (a KPI links the column), "
        "relationship (between source columns). More with get_lineage, get_relationships "
        "and search_documents."
    )
    if args.system_id is None:
        note += " Give the Source System's system_id to include its relationships."
    return _without_none(
        {
            "start": {
                "id": start.id,
                "type": start.type,
                "label": start.label,
                "layer": start.layer,
            },
            "depth": args.depth,
            "nodes": [
                {"id": n.id, "type": n.type, "label": n.label, "layer": n.layer, "distance": d}
                for n, d in nodes.values()
            ],
            "edges": [
                {**shown, "from": named[f], "to": named[t]}
                for (_, f, t), shown in edges.items()
                if f in named and t in named
            ],
            "nodes_left_out": len(left_out - nodes.keys()),
            **_mentions(ctx, start),
            "note": note,
        }
    )


def _mentions(ctx: ToolContext, start: Any) -> dict[str, Any]:
    """The passages that name the object, when the Workspace shares documents."""
    if not ctx.policy.allows(DataSharingLevel.DOCUMENTS):
        return {
            "documents": [],
            "documents_note": "Withheld: the Workspace's data-sharing level does not share "
            "documents.",
        }
    name = start.label.rsplit(".", 1)[-1] if start.type.endswith("column") else start.label
    passages = ctx.services.documents.search(
        ctx.user, ctx.workspace_id, name, limit=GRAPH_DOCUMENT_LIMIT
    )
    return {
        "documents": [
            {
                "file_id": p.file_id,
                "document": p.document,
                "section": p.section,
                "text": p.text[:GRAPH_PASSAGE_CHARS],
                "link": f"?folder=systems/{p.source_system_id}/documents&file={p.file_id}",
            }
            for p in passages
            if name.casefold() in p.text.casefold()
        ]
    }


def _get_score(ctx: ToolContext, args: _GetScore) -> Any:
    score = ctx.services.scores.current(ctx.user, ctx.workspace_id)
    return {
        "score": score.score,
        "grade": score.grade,
        "capped_at_c_by_errors": score.capped,
        "calculated_at": score.calculated_at,
        "layers": [
            {
                "layer": item.layer,
                "score": item.score if item.scored else "not scored",
                "grade": item.grade,
            }
            for item in score.layers
        ],
        "checks_failed": score.checks_failed,
        "failed_checks": [
            {
                "check": f.check_code,
                "object_id": f.object_id,
                "severity": f.severity,
                "layer": f.layer,
                "object": f.object_name,
                "problem": f.message,
                "fix_hint": f.fix_hint,
                "link": f.link,
            }
            for f in score.failed_checks[: args.limit]
        ],
    }


def _failed_check(ctx: ToolContext, args: _ExplainCheck) -> Any:
    score = ctx.services.scores.current(ctx.user, ctx.workspace_id)
    for check in score.failed_checks:
        if check.check_code == args.check_code and check.object_id == args.object_id:
            return check
    raise ApiError(
        404,
        "not_found",
        "That check does not fail on that object (any more); call get_score for the current ones.",
    )


def _explain_check(ctx: ToolContext, args: _ExplainCheck) -> Any:
    check = _failed_check(ctx, args)
    return {
        "check": check.check_code,
        "title": check.title,
        "category": check.category,
        "severity": check.severity,
        "layer": check.layer,
        "object_type": check.object_type,
        "object": check.object_name,
        "object_id": check.object_id,
        "table_id": check.table_id,
        "problem": check.message,
        "fix_hint": check.fix_hint,
        "link": check.link,
        "note": "Explain what the check measures, why it failed here and how it is fixed. "
        "Never quote sample values.",
    }


def _propose_check_fix(ctx: ToolContext, args: _ProposeCheckFix) -> Any:
    """The fix is not made: it becomes a Change Set the user reviews."""
    check = _failed_check(ctx, args)
    detail = ctx.services.change_sets.propose(
        ctx.user,
        ctx.workspace_id,
        origin="ai",
        scope={
            "kind": "check_fix",
            "check_code": check.check_code,
            "object_id": str(check.object_id),
        },
        title=args.title,
        conversation_id=ctx.conversation_id,
        items=[
            ProposedItem(
                key=f"fix{n}",
                object_type=c.object_type,
                operation=c.operation,
                object_id=c.object_id,
                payload=c.payload,
                label=c.label,
            )
            for n, c in enumerate(args.changes)
        ],
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "note": "The user reviews these changes; nothing has been changed yet.",
        "items": len(detail.items),
    }


def _evaluate_dw(ctx: ToolContext, args: _EvaluateDw) -> Any:
    evaluations = ctx.services.evaluations
    if args.findings is None:
        review = evaluations.review(ctx.user, ctx.workspace_id, args.layer)
        kpis = ctx.services.kpis.list(ctx.user, ctx.workspace_id, data_warehouse=True, limit=100)
        review["kpis"] = [
            {
                "id": k.id,
                "name": k.name,
                "formula_text": k.formula_text,
                "has_formula_sql": bool(k.formula_sql),
                "status": k.status,
            }
            for k in kpis.items
        ]
        review["note"] = (
            "Judge what the rule-based score cannot: ambiguous grain statements, dimensions "
            "that look like they need SCD2, missing conformed dimensions, KPIs this model "
            "cannot compute. Then call evaluate_dw again with your findings. Never quote "
            "sample values."
        )
        return review
    evaluation = evaluations.store(
        ctx.user,
        ctx.workspace_id,
        args.layer,
        [
            Finding(
                title=redact_text(f.title),
                detail=redact_text(f.detail),
                category=f.category,
                severity=f.severity,
                table_id=f.table_id,
                suggestion=redact_text(f.suggestion),
                items=[FindingItem(**c.model_dump()) for c in f.changes],
            )
            for f in args.findings
        ],
    )
    return {
        "evaluation_id": evaluation.id,
        "layer": evaluation.layer,
        "finding_count": len(evaluation.findings),
        "link": f"/workspaces/{ctx.workspace_id}/data-warehouse",
        "note": "Stored as advisory; it does not change the score. Editors can turn a finding "
        "into a Change Set.",
    }


def _search_documents(ctx: ToolContext, args: _SearchDocuments) -> Any:
    passages = ctx.services.documents.search(
        ctx.user,
        ctx.workspace_id,
        args.query,
        system_id=args.system_id,
        limit=args.limit,
        filters=PassageFilters(args.document_type, args.language, args.page, args.sheet, args.tag),
    )
    found = [
        {
            "file_id": p.file_id,
            "document": p.document,
            "section": p.section,
            "text": p.text,
            "document_type": p.document_type,
            "language": p.language,
            "page": p.page,
            "sheet": p.sheet,
            "source_system_id": p.source_system_id,
            "link": f"?folder=systems/{p.source_system_id}/documents&file={p.file_id}",
        }
        for p in passages
    ]
    if found:
        return {"passages": found}
    return {
        "passages": [],
        "note": "Nothing relevant found in the documents for this query. Say so; do not "
        "guess an answer from them.",
    }


def _search_workspace(ctx: ToolContext, args: _SearchWorkspace) -> Any:
    kinds = set(args.kinds or ("source", "dw", "kpi", "document"))
    services = ctx.services
    needle = args.query.strip().lower()
    found: dict[str, Any] = {}
    if "source" in kinds:
        hits: list[Any] = []
        page = services.systems.list(ctx.user, ctx.workspace_id)
        for system in page.items:
            try:
                results = _search_catalog(
                    ctx, _SearchCatalog(system_id=system.id, query=args.query, limit=args.limit)
                )
            except ApiError:
                continue  # no Snapshot yet
            hits.extend({"system_id": system.id, "system": system.name, **r} for r in results)
        found["source"] = hits[: args.limit]
    if "dw" in kinds:
        found["dw"] = _search_dw(ctx, needle, args.limit)
    if "kpi" in kinds:
        matches = []
        cursor = None
        while True:
            kpis = services.kpis.list(ctx.user, ctx.workspace_id, limit=100, cursor=cursor)
            matches.extend(
                {
                    "id": k.id,
                    "name": k.name,
                    "definition": k.definition,
                    "source_system_id": k.source_system_id,
                    "link": f"/workspaces/{ctx.workspace_id}/kpis",
                }
                for k in kpis.items
                if needle in k.name.lower() or needle in k.definition.lower()
            )
            cursor = kpis.next_cursor
            if cursor is None or len(matches) >= args.limit:
                break
        found["kpi"] = matches[: args.limit]
    if "document" in kinds:
        if ctx.policy.allows(DataSharingLevel.DOCUMENTS):
            found["document"] = _search_documents(
                ctx, _SearchDocuments(query=args.query, limit=args.limit)
            )["passages"]
        else:
            found["document"] = []
            found["document_note"] = (
                "Withheld: the Workspace's data-sharing level does not share documents."
            )
    return found


def _search_dw(ctx: ToolContext, needle: str, limit: int) -> list[dict[str, Any]]:
    """Data Warehouse tables whose name matches, and tables with a column whose name matches."""
    model = ctx.services.model
    out: list[dict[str, Any]] = []
    for summary in model.list_tables(ctx.user, ctx.workspace_id):
        if len(out) >= limit:
            break
        link = f"/workspaces/{ctx.workspace_id}/data-warehouse"
        if needle in summary.name.lower():
            out.append(
                {
                    "kind": "table",
                    "id": summary.id,
                    "name": summary.name,
                    "layer": summary.layer,
                    "link": link,
                }
            )
            continue
        table = model.get_table(ctx.user, ctx.workspace_id, summary.id)
        for column in table.columns:
            if needle in column.name.lower() and len(out) < limit:
                out.append(
                    {
                        "kind": "column",
                        "id": column.id,
                        "name": column.name,
                        "table": summary.name,
                        "table_id": summary.id,
                        "layer": summary.layer,
                        "link": link,
                    }
                )
    return out


def _describe_image(ctx: ToolContext, args: _DescribeImage) -> Any:
    image = ctx.services.documents.describe_image(ctx.user, ctx.workspace_id, args.file_id)
    return {"file_id": image.file_id, "name": image.name, "description": image.description}


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


class _RunValidation(BaseModel):
    pass


def _run_validation(ctx: ToolContext, args: _RunValidation) -> Any:
    report = ctx.services.validation.run_validation(ctx.user, ctx.workspace_id)
    return {
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "problems": report.problems,
        "coverage": report.coverage,
        "link": f"/workspaces/{ctx.workspace_id}/data-warehouse",
    }


def _unified_diff(name: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{name} (saved)",
            tofile=f"{name} (new)",
        )
    )[:MAX_FILE_TEXT_CHARS]


def _export(ctx: ToolContext, args: _GenerateFile) -> tuple[str, bytes]:
    """The name and bytes of the file: the exporters' for DDL and XLSX, the model's text else."""
    services = ctx.services
    if args.kind == "ddl":
        package = services.ddl.export(ctx.user, ctx.workspace_id, layer=args.layer)
        return package.name, package.data
    if args.kind == "mapping_sheet":
        if args.layer == "staging":
            raise ApiError(422, "invalid_layer", "The mapping sheet covers Core and Mart.")
        sheet = services.mapping_exports.export(
            ctx.user, ctx.workspace_id, fmt="xlsx", layer=args.layer
        )
        return sheet.name, sheet.data
    if args.kind == "data_dictionary":
        if args.system_id is None:
            raise ApiError(422, "invalid_request", "A data dictionary needs a system_id.")
        dictionary = services.dictionaries.export(ctx.user, ctx.workspace_id, args.system_id)
        return dictionary.name, dictionary.data
    if not args.name:
        raise ApiError(422, "invalid_request", "A text file needs a name.")
    # Model-written text may quote a value it saw: validator-detectable PII is redacted.
    return redact_text(args.name).strip(), redact_text(args.content).encode()


def _generate_file(ctx: ToolContext, args: _GenerateFile) -> Any:
    name, data = _export(ctx, args)
    files = ctx.services.files
    # DDL and the mapping sheet describe the Data Warehouse, so they go to its file area.
    system_id = None if args.kind in ("ddl", "mapping_sheet") else args.system_id
    if system_id is None:
        existing = files.find_in_warehouse(ctx.user, ctx.workspace_id, name)
    else:
        existing = files.find_in_system(ctx.user, ctx.workspace_id, system_id, name)
    if existing is not None:
        name = existing.name
    if existing is not None and not args.replace:
        # Files are overwritten in place with no history, so the user sees the change first.
        try:
            before: str | None = files.read_text(ctx.user, ctx.workspace_id, existing.id).content
        except ApiError:  # a binary file has no text to compare
            before = None
        after = data.decode(errors="replace") if args.kind in ("text", "ddl") else None
        if before is not None and before == after:
            return {"status": "unchanged", "name": name, "note": "The file already has this text."}
        diff = None if before is None or after is None else _unified_diff(name, before, after)
        return {
            "status": "needs_confirmation",
            "name": name,
            "diff": diff,
            "note": "A file with this name exists and was NOT changed. Show the user "
            + ("this diff and ask" if diff else "that and ask")
            + " whether to overwrite it; only if they agree, call generate_file again "
            "with the same arguments and replace=true.",
        }
    if system_id is None:
        return files.save_generated_to_warehouse(ctx.user, ctx.workspace_id, name=name, data=data)
    return files.save_generated(ctx.user, ctx.workspace_id, system_id, name=name, data=data)


def _update_file(ctx: ToolContext, args: _UpdateFile) -> Any:
    """The file is not changed: the new text becomes a Change Set the user accepts."""
    content = redact_text(args.content)
    current = ctx.services.files.read_text(ctx.user, ctx.workspace_id, args.file_id)
    if current.content == content:
        return {"status": "unchanged", "name": current.file.name}
    detail = ctx.services.change_sets.propose(
        ctx.user,
        ctx.workspace_id,
        origin="ai",
        scope={"kind": "file", "file_id": str(args.file_id)},
        title=args.title,
        conversation_id=ctx.conversation_id,
        items=[
            ProposedItem(
                key="file",
                object_type="file",
                operation="update",
                object_id=args.file_id,
                payload={"content": content},
                label=current.file.name,
            )
        ],
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "name": current.file.name,
        "diff": _unified_diff(current.file.name, current.content, content),
        "note": "The user reviews this diff; the file has not been changed yet.",
    }


def _suggest_kpis(ctx: ToolContext, args: _SuggestKpis) -> Any:
    result = ctx.services.kpis.suggest(
        ctx.user,
        ctx.workspace_id,
        [KpiSuggestion(**idea.model_dump()) for idea in args.suggestions],
        source_system_id=args.system_id,
    )
    folder = "dw" if args.system_id is None else f"systems/{args.system_id}"
    return {
        "created": [
            {
                "id": kpi.id,
                "name": kpi.name,
                "status": kpi.status,
                "origin": kpi.origin,
                "link": f"?folder={folder}/kpis",
            }
            for kpi in result.created
        ],
        "skipped": result.skipped,
        "note": "Created as AI-labelled drafts. Names that already exist were skipped; "
        "existing KPIs are never changed.",
    }


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
    # A one-row result's COUNT columns may be cited as evidence: counts, never values.
    counts = [
        {"column": result.columns[i], "count": cell}
        for i in result.count_ordinals
        if result.row_count == 1
        and isinstance(cell := result.rows[0][i], int)
        and not isinstance(cell, bool)
    ]
    if counts:
        ref = new_query_ref()
        ctx.counts[ref] = CountEvidence(
            args.system_id, redact_text(args.sql, entities=False), counts
        )
        content["query_ref"] = ref
        content["query_ref_note"] = (
            "Cite this query_ref in propose_changes to attach its counts as evidence."
        )
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
    items = _proposed_items(ctx, args)
    if ctx.proposals is not None:  # in a job: one Change Set when the job succeeds
        proposals = ctx.proposals
        proposals.check(args.source_system_id, items)
        # Validated now, so a bad object is reported to the model rather than failing the job.
        ctx.services.change_sets.validate(
            ctx.user,
            ctx.workspace_id,
            scope={
                "kind": "source_enhancements",
                "source_system_id": str(args.source_system_id),
            },
            items=items,
            earlier_keys=[item.key for item in proposals.items],
        )
        total = proposals.add(args.source_system_id, items)
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
        title=redact_text(args.title),
        conversation_id=ctx.conversation_id,
        items=items,
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "note": "The user reviews these changes; nothing has been changed yet.",
        "items": len(detail.items),
    }


def _start_job(ctx: ToolContext, args: _StartJob) -> Any:
    return _submit_job(ctx, args.title, args.task)


def _submit_job(ctx: ToolContext, title: str, task: str) -> Reported:
    job = ctx.services.jobs.submit(
        ctx.workspace_id,
        ASSISTANT_JOB,
        {
            "user_id": str(ctx.user.id),
            "workspace_id": str(ctx.workspace_id),
            "title": title,
            "conversation_id": str(ctx.conversation_id) if ctx.conversation_id else None,
            "task": task,
        },
        title=title,
        created_by=ctx.user.id,
        priority=PRIORITY_CHAT,
    )
    content = {
        "job_id": job.id,
        "status": job.status,
        "note": "The job runs in the background and the member follows it in the chat. "
        "Tell them it started; do not wait for its result.",
    }
    return Reported(content, {"job_id": str(job.id), "title": job.title})


GENERATION_TITLE = "Generate the Core and Mart Layers"
GENERATION_TASK = (
    "Generate the Data Warehouse's Core and Mart Layers (Kimball facts and dimensions) from "
    "the staging tables, the Source Schemas, the KPIs and the documents. First call "
    "generate_dw_schema without `tables` to read the inputs; look up source tables, KPIs or "
    "documents with your other tools where that helps; then call generate_dw_schema once with "
    "the whole model in `tables`. If it lists problems, fix them and call it again with the "
    "whole model."
)
TABLE_GENERATION_TASK = (
    "Regenerate only the Data Warehouse table {table}, following the member's mapping of it. "
    "First call generate_dw_schema with `table`: {table!r} and without `tables` to read the "
    "inputs (`regenerate_only` names the table, its handle and what its mapping reads); then "
    "call generate_dw_schema with `table`: {table!r} and `tables` holding only that table, by "
    "its handle. If it lists problems, fix them and call it again."
)
GENERATION_NOTE = (
    "Design the Core Layer as a Kimball star schema from the staging tables: facts with a "
    "grain statement and fact type; dimensions with an SCD type (2 where history matters) and "
    "their natural keys (role nk); measures with an additivity; foreign keys (role fk) whose "
    "`references` is a dimension's handle or name. DAWAM adds surrogate keys, SCD2 columns and "
    "unknown members itself. A dimension shared by several facts is conformed. Every fact can "
    "reference the date dimension (`references`: its handle, or `date`); never model one. Give "
    "a Core fact's foreign key a `lookup` with the staging natural-key columns (and the "
    "as-of date for an SCD2 dimension). A column's `data_type.type` is one of smallint, "
    "integer, bigint, decimal, float, double, boolean, char, string, text, binary, date, "
    "time, timestamp, timestamptz, uuid, json. Add Mart facts or aggregates for the KPIs; Mart "
    "tables reference Core conformed dimensions. Keep every existing table or column you still "
    "want by giving its `handle` (a new name is a rename); leave the handle out only for "
    "something new. Never propose anything in `deleted_by_users`. Follow the naming rules."
)


class _GenLookup(BaseModel):
    nk_inputs: list[str] = Field(
        min_length=1,
        max_length=10,
        description="The dimension's natural-key staging columns, as `staging_table.column`.",
    )
    as_of_input: str | None = Field(
        default=None,
        max_length=300,
        description="SCD2: the staging date column, as `staging_table.column`.",
    )


class _GenColumn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    handle: str | None = Field(
        default=None, max_length=40, description="The existing column's handle, if it is one."
    )
    data_type: dict[str, Any] = Field(
        description="`{type, length?, precision?, scale?}`; see the note for the types."
    )
    role: Literal["sk", "nk", "fk", "measure", "attribute", "degenerate_dimension", "audit"]
    is_nullable: bool = True
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = Field(
        default=None, description="Required for a measure."
    )
    scd_type_override: Literal[0, 1, 2] | None = None
    references: str | None = Field(
        default=None,
        max_length=300,
        description="A foreign key's dimension: handle, name or `date`.",
    )
    role_name: str | None = Field(
        default=None, max_length=128, description="For a role-playing foreign key."
    )
    description: str = Field(default="", max_length=4000)
    semantic_type: str | None = Field(default=None, max_length=64)
    lookup: _GenLookup | None = None


class _GenTable(BaseModel):
    handle: str | None = Field(
        default=None, max_length=40, description="The existing table's handle, if it is one."
    )
    layer: Literal["core", "mart"]
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["fact", "dimension", "bridge"]
    description: str = Field(default="", max_length=4000)
    grain: str | None = Field(
        default=None, max_length=1000, description="A fact's grain: what one row stands for."
    )
    fact_type: (
        Literal["transactional", "periodic_snapshot", "accumulating_snapshot", "factless"] | None
    ) = None
    is_aggregate: bool = False
    scd_type: Literal[0, 1, 2] | None = Field(default=None, description="A dimension's SCD type.")
    is_conformed: bool = False
    columns: list[_GenColumn] = Field(default_factory=list, max_length=300)


class _GenerateDwSchema(BaseModel):
    tables: list[_GenTable] | None = Field(
        default=None,
        max_length=300,
        description="Leave out to read the inputs. Then the whole model.",
    )
    table: str | None = Field(
        default=None,
        max_length=300,
        description="Regenerate only this table (handle or `layer.name`); `tables` then holds "
        "only it.",
    )


def _generated_table(table: _GenTable) -> GeneratedTable:
    return GeneratedTable(
        **table.model_dump(exclude={"columns"}),
        columns=[
            GeneratedColumn(
                **column.model_dump(exclude={"lookup"}),
                lookup=column.lookup.model_dump() if column.lookup else None,
            )
            for column in table.columns
        ],
    )


def _generate_dw_schema(ctx: ToolContext, args: _GenerateDwSchema) -> Any:
    """Generation is a long task: from a chat it starts a job; the job reads the inputs, then
    hands the whole model to DAWAM, which plans the job's one Change Set."""
    generation = ctx.services.generation
    if ctx.proposals is None:
        # Refused, not set up or an unknown table: say so now.
        scoped = generation.inputs(ctx.user, ctx.workspace_id, table=args.table)
        if args.table is None:
            reported = _submit_job(ctx, GENERATION_TITLE, GENERATION_TASK)
        else:
            only = scoped["regenerate_only"]
            name = f"{only['layer']}.{only['name']}"
            reported = _submit_job(
                ctx, f"Regenerate {name}", TABLE_GENERATION_TASK.format(table=name)
            )
        reported.content["note"] = (
            "DW generation runs as a background job and ends in one Change Set for the member "
            "to review. Tell them it started; do not wait for its result."
        )
        return reported
    if args.tables is None:
        inputs = generation.inputs(ctx.user, ctx.workspace_id, table=args.table)
        kpis = ctx.services.kpis.list(ctx.user, ctx.workspace_id, limit=100)
        inputs["kpis"] = [
            {
                "name": k.name,
                "definition": k.definition,
                "formula_text": k.formula_text,
                "unit": k.unit,
                "aggregation": k.aggregation,
            }
            for k in kpis.items
        ]
        inputs["note"] = GENERATION_NOTE
        return inputs
    try:
        plan = generation.plan(
            ctx.user, ctx.workspace_id, [_generated_table(t) for t in args.tables], table=args.table
        )
    except ApiError as exc:
        if exc.code != "invalid_generation":
            raise
        return {
            "status": "not_collected",
            "problems": (exc.details or {}).get("problems", []),
            "note": f"{exc.message} Nothing was collected; call again with the whole model.",
        }
    ctx.proposals.set_generation(plan)
    return {
        "status": "collected",
        "items": len(plan.items),
        "conflicts": plan.conflicts,
        "notes": [dataclasses.asdict(n) for n in plan.notes],
        "note": "These changes are the job's Change Set, which the member reviews when the job "
        "finishes; nothing has been changed yet. Calling again replaces them.",
    }


MAPPING_DRAFT_TITLE = "Draft mappings"
MAPPING_DRAFT_NOTE = (
    "Draft each target table as one or more branches (a table fed by several staging tables or "
    "systems has one branch for each; branches are combined with UNION ALL). A branch names "
    "its `driving_input` (a table of the Layer below: staging for Core, Core for Mart), its "
    "`joins` and `filters` (SQL parts in the target platform's dialect) and, for an aggregate "
    "table, `group_by`. Map each column in `columns` with a plain-language `rule_text` and, for "
    "direct, derived or constant, a `sql_expression` in the dialect, writing every input as "
    "table.column of the Layer below. `direct` is one column; `constant` reads none. A Core "
    "fact's foreign key (role fk) is a `lookup` with the staging natural-key columns of its "
    "dimension (and the as-of date for an SCD2 one) as `staging_table.column`; give no SQL. "
    "Use `not_in_branch` for a column a branch cannot supply (it becomes NULL). Skip surrogate "
    "keys and SCD or audit columns: DAWAM maps them. Reuse the name of an existing branch to "
    "fill its unmapped columns; mapped columns are never overwritten."
)


class _DraftLookup(BaseModel):
    nk_inputs: list[str] = Field(
        min_length=1,
        max_length=10,
        description="The dimension's natural-key staging columns, as `staging_table.column`.",
    )
    as_of_input: str | None = Field(
        default=None,
        max_length=300,
        description="SCD2: the date column, as `staging_table.column`.",
    )


class _DraftColumn(BaseModel):
    column: str = Field(min_length=1, max_length=128, description="The target column's name.")
    mapping_type: Literal["direct", "derived", "constant", "lookup", "not_in_branch"]
    sql_expression: str = Field(default="", max_length=20000)
    rule_text: str = Field(default="", max_length=4000)
    lookup: _DraftLookup | None = None


class _DraftBranch(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    driving_input: str = Field(min_length=1, max_length=500)
    joins: str = Field(default="", max_length=20000)
    filters: str = Field(default="", max_length=20000)
    group_by: str | None = Field(default=None, max_length=20000)
    having: str | None = Field(default=None, max_length=20000)
    columns: list[_DraftColumn] = Field(default_factory=list, max_length=300)


class _DraftTableMappings(BaseModel):
    table: str = Field(min_length=1, max_length=300, description="`name` or `layer.name`.")
    branches: list[_DraftBranch] = Field(min_length=1, max_length=20)


class _DraftMappings(BaseModel):
    tables: list[str] | None = Field(
        default=None, max_length=100, description="The target tables (`name` or `layer.name`)."
    )
    layer: Literal["core", "mart"] | None = Field(
        default=None, description="Instead of `tables`: all tables of this Layer."
    )
    drafts: list[_DraftTableMappings] | None = Field(
        default=None,
        max_length=10,
        description="Leave out to read the inputs. Then the tables' mappings.",
    )


def _draft_table(table: _DraftTableMappings) -> DraftTable:
    return DraftTable(
        table.table,
        [
            DraftBranch(
                **branch.model_dump(exclude={"columns"}),
                columns=[
                    DraftColumn(
                        **column.model_dump(exclude={"lookup"}),
                        lookup=column.lookup.model_dump() if column.lookup else None,
                    )
                    for column in branch.columns
                ],
            )
            for branch in table.branches
        ],
    )


def _draft_mappings(ctx: ToolContext, args: _DraftMappings) -> Any:
    """Several tables are a long task: from a chat they start a job that reads the inputs a
    few tables at a time and ends in one Change Set. One table is drafted in the chat."""
    drafts = ctx.services.mapping_drafts
    if args.drafts is None:
        if not args.tables and args.layer is None:
            raise ApiError(422, "invalid_mapping_draft", "Name the tables, or a layer.")
        names = drafts.resolve(ctx.user, ctx.workspace_id, args.tables or (), args.layer)
        if ctx.proposals is None and len(names) > 1:
            reported = _submit_job(
                ctx,
                MAPPING_DRAFT_TITLE,
                "Draft the mappings of these Core and Mart tables: "
                + ", ".join(names)
                + ". Call draft_mappings with `tables` (at most 10 at a time) to read the "
                "inputs, look up source tables, KPIs or documents with your other tools where "
                "that helps, then call draft_mappings with `drafts` for the tables you read. "
                "If it lists problems, fix them and call it again. Repeat until every table "
                "is drafted.",
            )
            reported.content["note"] = (
                "Mapping drafts run as a background job and end in one Change Set for the "
                "member to review. Tell them it started; do not wait for its result."
            )
            return reported
        if len(names) > MAX_DRAFT_INPUTS:
            return {
                "tables": names,
                "note": f"Call again with `tables` set to at most {MAX_DRAFT_INPUTS} of these "
                "to read their inputs.",
            }
        inputs = drafts.inputs(ctx.user, ctx.workspace_id, names)
        inputs["note"] = MAPPING_DRAFT_NOTE
        return inputs
    try:
        plan = drafts.plan(ctx.user, ctx.workspace_id, [_draft_table(t) for t in args.drafts])
    except ApiError as exc:
        if exc.code != "invalid_mapping_draft":
            raise
        return {
            "status": "not_collected",
            "problems": (exc.details or {}).get("problems", []),
            "note": f"{exc.message} Call again with the whole draft of these tables.",
        }
    notes = [dataclasses.asdict(n) for n in plan.notes]
    if ctx.proposals is not None:
        total = ctx.proposals.add_mapping_draft(plan)
        return {
            "status": "collected",
            "items_so_far": total,
            "notes": notes,
            "note": "These changes join the job's Change Set, which the member reviews when "
            "the job finishes; nothing has been changed yet. Drafting a table again "
            "replaces its earlier draft.",
        }
    if not plan.items:
        return {"status": "nothing_to_draft", "notes": notes}
    detail = ctx.services.change_sets.propose(
        ctx.user,
        ctx.workspace_id,
        origin=plan.origin,
        scope=plan.scope,
        title=MAPPING_DRAFT_TITLE,
        conversation_id=ctx.conversation_id,
        items=plan.items,
    )
    return {
        "change_set_id": detail.change_set.id,
        "status": "proposed",
        "items": len(detail.items),
        "notes": notes,
        "note": "The member reviews these changes; nothing has been changed yet.",
    }


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
        "get_relationships",
        "List a Source System's inferred relationships (default: the suggested ones) with "
        "their confidence and evidence, to confirm or reject them.",
        _GetRelationships,
        "read",
        Action.VIEW_WORKSPACE,
        _get_relationships,
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
        "get_lineage",
        "Column-level lineage of a node (source column, DW column or table, KPI): the nodes "
        "and edges upstream (where it comes from) or downstream (what depends on it). Edge "
        "kinds: value (feeds the value), uses (a join, filter or GROUP BY steers the table), "
        "lookup (fact key to dimension), kpi (column a KPI links).",
        _GetLineage,
        "read",
        Action.VIEW_WORKSPACE,
        _get_lineage,
    ),
    Tool(
        "explore_graph",
        "An object's neighbours (source column, DW column or table, KPI) at most 2 hops "
        "away, in one call: lineage, KPI links, a source column's relationships (give "
        "system_id) and the documents that mention it (when the Workspace shares documents).",
        _ExploreGraph,
        "read",
        Action.VIEW_WORKSPACE,
        _explore_graph,
    ),
    Tool(
        "get_score",
        "The Data Warehouse's rule-based design score (0-100) and grade, each Layer's score, "
        "and the failed checks with severity, the object at fault and a fix hint.",
        _GetScore,
        "read",
        Action.VIEW_WORKSPACE,
        _get_score,
    ),
    Tool(
        "explain_check",
        "Read one failed score check (by `check_code` and `object_id` from get_score) to "
        "explain what it measures and why it failed.",
        _ExplainCheck,
        "read",
        Action.ASK_ASSISTANT,
        _explain_check,
    ),
    Tool(
        "propose_check_fix",
        "Propose the exact changes that fix a failed score check. Nothing changes until an "
        "editor accepts the proposal as a Change Set. Explain first with explain_check.",
        _ProposeCheckFix,
        "write",
        Action.REVIEW_CHANGE_SETS,
        _propose_check_fix,
    ),
    Tool(
        "evaluate_dw",
        "Advisory evaluation of a Data Warehouse Layer. Call with only `layer` to read it "
        "(tables, grain, SCD types, columns, KPIs), then again with your `findings` to "
        "store them. Findings never change the score or the gate.",
        _EvaluateDw,
        "read",
        Action.ASK_ASSISTANT,
        _evaluate_dw,
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
        "search_workspace",
        "Find things by name across the Workspace in one call: Source System tables, views, "
        "columns and routines (source), Data Warehouse tables and columns (dw), KPIs (kpi) "
        "and uploaded documents (document, when the Workspace shares them). Each match "
        "carries its id; open it with get_object.",
        _SearchWorkspace,
        "read",
        Action.VIEW_WORKSPACE,
        _search_workspace,
    ),
    Tool(
        "describe_image",
        "Look at an uploaded image (an ERD, a diagram, a report mock-up) and get a text "
        "description of it. Use a file_id from search_documents or list_files.",
        _DescribeImage,
        "read",
        Action.VIEW_WORKSPACE,
        _describe_image,
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
        "workspace_overview",
        "List the Workspace: its Source Systems (id, name, engine, Connection and Snapshot "
        "status, file count), the Data Warehouse's tables per Layer, the KPI count and file "
        "areas, with ids and links. Start here when you need an id.",
        _WorkspaceOverview,
        "read",
        Action.VIEW_WORKSPACE,
        _workspace_overview,
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
        "run_validation",
        "Validate the Core and Mart mappings of the Data Warehouse: errors (SQL that does not "
        "parse, GROUP BY gaps), warnings (unmapped columns, data-type truncation) and the "
        "mapping coverage per table, Layer and Data Warehouse.",
        _RunValidation,
        "read",
        Action.VIEW_WORKSPACE,
        _run_validation,
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
        "Save a file in a file area: text you write (Markdown such as a gaps & enhancements "
        "report, KPI documentation or a documentation page; never quote sample values), or "
        "an export (`ddl`, `mapping_sheet`, `data_dictionary`) produced by the exporters. "
        "Without a system_id it goes to the Data Warehouse's file area. If a file of that name "
        "exists it is not changed: you get a diff to show the user, and overwrite only after "
        "they agree (replace=true).",
        _GenerateFile,
        "write",
        Action.UPLOAD_FILE,
        _generate_file,
    ),
    Tool(
        "update_file",
        "Propose a new text for an existing text file. The user sees it as a diff in a Change "
        "Set and the file is saved only when they accept. Read the file first and send its "
        "complete new text.",
        _UpdateFile,
        "write",
        Action.UPLOAD_FILE,
        _update_file,
    ),
    Tool(
        "suggest_kpis",
        "After reading a Source System's schema, profiles and documents, propose KPIs. They "
        "are created as AI-labelled drafts with your rationale; a name that already exists "
        "is skipped and existing KPIs are never changed. Never put sample values or "
        "personal data in the text.",
        _SuggestKpis,
        "write",
        Action.EDIT_KPI,
        _suggest_kpis,
    ),
    Tool(
        "propose_changes",
        "Propose descriptions (including the meaning of codes and statuses), tags, sensitivity "
        "flags, classifications and SCD hints for a Source System's tables and columns, and "
        "accept or reject its inferred relationships. Nothing changes until the user accepts "
        "the Change Set. Never put sample values or personal data in the text.",
        _ProposeChanges,
        "write",
        Action.REVIEW_CHANGE_SETS,
        _propose_changes,
        compact=True,
    ),
    Tool(
        "get_kpi_link_candidates",
        "List the KPIs that have no links to DW columns or no formula SQL, with the Data "
        "Warehouse's Core and Mart tables and columns (ids included) to propose them from.",
        _GetKpiLinkCandidates,
        "read",
        Action.REVIEW_CHANGE_SETS,
        _get_kpi_link_candidates,
    ),
    Tool(
        "propose_kpi_links",
        "Propose links from KPIs to DW columns and their formula SQL against the DW Schema. "
        "Nothing changes until the user accepts the proposal as a Change Set.",
        _ProposeKpiLinks,
        "write",
        Action.REVIEW_CHANGE_SETS,
        _propose_kpi_links,
    ),
    Tool(
        "generate_dw_schema",
        "Generate or regenerate the Data Warehouse's Core and Mart Layers (Kimball facts and "
        "dimensions) as one Change Set the member reviews. It runs as a background job; in a "
        "job, call it without `tables` to read the inputs, then with the whole model.",
        _GenerateDwSchema,
        "write",
        Action.EDIT_DW_SCHEMA,
        _generate_dw_schema,
        compact=True,
    ),
    Tool(
        "draft_mappings",
        "Draft the mappings (branches, column mappings, lookups) of Core or Mart tables as one "
        "Change Set the member reviews. Several tables run as a background job. Call it with "
        "`tables` or `layer` to read the inputs, then with `drafts`.",
        _DraftMappings,
        "write",
        Action.EDIT_DW_SCHEMA,
        _draft_mappings,
        compact=True,
    ),
    Tool(
        "render_diagram",
        "Draw the Data Warehouse's star schema (facts and their dimensions) or the lineage "
        "around a node as a Mermaid diagram built from DAWAM's own model. Put the returned "
        "`mermaid` text in a ```mermaid block of your answer.",
        _RenderDiagram,
        "read",
        Action.VIEW_WORKSPACE,
        _render_diagram,
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
        # Sorted by name: the tool list is part of the cached prompt prefix, so it must be
        # byte-identical from one call to the next.
        return sorted(
            (
                tool.spec
                for tool in self._registry.all()
                if self._offered(tool)
                and self._registry.allowed(self._ctx, self._policy, tool) is None
            ),
            key=lambda spec: spec.name,
        )

    def _offered(self, tool: Tool) -> bool:
        return not (tool.chat_only and self._ctx.proposals is not None)

    def submit_proposals(self, title: str) -> int:
        """Make the changes a job collected into one Change Set; how many items it has (0:
        none were proposed, so there is no Change Set)."""
        proposals = self._ctx.proposals
        if proposals is not None and proposals.generation is not None:
            plan = proposals.generation
            if not plan.items:
                return 0
            detail = self._ctx.services.change_sets.propose(
                self._ctx.user,
                self._ctx.workspace_id,
                origin=plan.origin,
                scope=plan.scope,
                title=title,
                conversation_id=self._ctx.conversation_id,
                items=plan.items,
            )
            return len(detail.items)
        if proposals is not None and proposals.mapping_tables:
            draft = proposals.mapping_draft()
            if not draft.items:
                return 0
            detail = self._ctx.services.change_sets.propose(
                self._ctx.user,
                self._ctx.workspace_id,
                origin=draft.origin,
                scope=draft.scope,
                title=title,
                conversation_id=self._ctx.conversation_id,
                items=draft.items,
            )
            return len(detail.items)
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
            title=redact_text(title),
            conversation_id=self._ctx.conversation_id,
            items=proposals.items,
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
