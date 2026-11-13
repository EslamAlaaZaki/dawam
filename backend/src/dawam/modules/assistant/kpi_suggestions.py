"""AI KPI suggestions for one Source System, as a background job (spec §6.13, story 75).

An owner or editor clicks **Suggest KPIs** on a Source System's KPIs page (``start``): a
``kpi_suggest`` job (``run`` is its handler) runs the agent loop as that member with a scope
the server pins. The model gets lookup tools for that system only (its Source Schema,
profiles, relationships and documents; ``system_id`` is filled in by the job and is not an
argument it can set) and ``suggest_kpis`` for that system. The task lists the system's tables
and every KPI documented under it in every review state, so it suggests only different ones.
``KpiService.suggest`` drops a suggestion based on a table outside the system and skips a
name already there; the job reports what was created, dropped and skipped as its result.
Suggesting nothing is a normal outcome. One job per Source System runs at a time.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.jobs import PRIORITY_NORMAL, Job, JobCancelledError, JobContext
from dawam.modules.llm import Message, RoleService, ToolSpec, WorkspaceAiService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_text

from .internal.agent import Finished, ToolFinished, ToolOutcome, run_agent
from .internal.prompt import SYSTEM_PROMPT, quote_data, today_line
from .internal.tools import KPI_SUGGESTION_RULES, ToolRegistry

KPI_SUGGEST_JOB = "kpi_suggest"
MAX_TOOL_CALLS = 40
"""A suggestion job reads a few tables and suggests once: far below an assistant job's cap."""
MAX_LISTED_TABLES = 300
"""Tables named in the task; the model finds the others with ``search_catalog``."""
ACTIVE = ("queued", "running", "paused")
STOP_POLL_SECONDS = 1.0

LOOKUPS = frozenset(
    {"search_catalog", "get_object", "get_profile", "get_relationships", "search_documents"}
)
SUGGEST = "suggest_kpis"

KPI_JOB_PROMPT = """\


Background job, KPI suggestions:
- You suggest KPIs for one Source System, named in the task. Your tools read that system \
only, and suggest_kpis documents KPIs under it.
- Read what you need (tables, profiles, relationships, documents), then call suggest_kpis \
once with your suggestions, each with a short rationale and the tables it is based on.
- If nothing new fits, do not call suggest_kpis: say so. That is a normal outcome.
- Finish with one short sentence.\
"""


class ScopedKpiTools:
    """A job's bound tools narrowed to one Source System: only the lookups and
    ``suggest_kpis`` are offered, ``system_id`` is gone from their schemas and every call
    runs with the job's system. What ``suggest_kpis`` created, dropped and skipped is
    counted."""

    def __init__(self, tools: Any, system_id: uuid.UUID) -> None:
        self._tools = tools
        self._system_id = system_id
        self._names = LOOKUPS | {SUGGEST}
        self.created = 0
        self.dropped = 0
        self.skipped = 0

    def specs(self) -> Sequence[ToolSpec]:
        return [_unscoped(s) for s in self._tools.specs() if s.name in self._names]

    def is_read(self, name: str, arguments: dict[str, Any]) -> bool:
        return name in LOOKUPS

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name not in self._names:
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        if name == "get_object" and arguments.get("kind") == "kpi":
            return ToolOutcome("The KPIs of this Source System are listed in the task.", "error")
        pinned = {**arguments, "system_id": str(self._system_id)}
        outcome = self._tools.execute(name, pinned)  # as the member, authorised again
        if name == SUGGEST and outcome.status == "ok" and arguments.get("suggestions"):
            result = json.loads(outcome.content)
            self.created += len(result.get("created", []))
            self.dropped += len(result.get("dropped", []))
            self.skipped += len(result.get("skipped", []))
        return outcome


def _unscoped(spec: ToolSpec) -> ToolSpec:
    """The spec without ``system_id`` (the job sets it); ``get_object`` reads source objects
    only, and ``suggest_kpis`` must be sent suggestions."""
    parameters = json.loads(json.dumps(spec.parameters))
    parameters.get("properties", {}).pop("system_id", None)
    required = [r for r in parameters.get("required", []) if r != "system_id"]
    if spec.name == "get_object":
        parameters["properties"]["kind"]["enum"] = ["table", "column", "routine"]
    if spec.name == SUGGEST:
        required.append("suggestions")
    if required:
        parameters["required"] = required
    else:
        parameters.pop("required", None)
    return ToolSpec(spec.name, spec.description, parameters)


class KpiSuggestionJobs:
    """Starts, reads and runs ``kpi_suggest`` jobs. Every method that acts for a user
    authorizes through the workspaces module's policy."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        roles: RoleService,
        ai: WorkspaceAiService,
        tools: ToolRegistry,
        users: Callable[[uuid.UUID], User | None],
        timer: Callable[[], float] = time.monotonic,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._roles = roles
        self._ai = ai
        self._tools = tools
        self._services = tools.services
        self._users = users
        self._timer = timer
        self._cap = max_tool_calls

    def start(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Job:
        """Queue a job that suggests KPIs for one Source System (owners and editors who may
        ask the assistant). 404 if the system is not in the Workspace; 409
        ``kpi_suggestions_running`` while one runs for it already."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        system = self._services.systems.get(user, workspace_id, system_id)
        jobs = self._services.jobs
        with Session(self._engine) as db, db.begin():
            # One job per Source System: two clicks at once queue one job.
            key = f"kpi_suggest:{system_id}"
            db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
            current = jobs.latest(
                workspace_id, KPI_SUGGEST_JOB, {"source_system_id": str(system_id)}, db=db
            )
            if current is not None and current.status in ACTIVE:
                raise ApiError(
                    409,
                    "kpi_suggestions_running",
                    "KPIs are already being suggested for this Source System.",
                    {"job_id": str(current.id)},
                )
            return jobs.submit(
                workspace_id,
                KPI_SUGGEST_JOB,
                {
                    "workspace_id": str(workspace_id),
                    "source_system_id": str(system_id),
                    "user_id": str(user.id),
                },
                title=f"Suggest KPIs: {system.name}",
                created_by=user.id,
                priority=PRIORITY_NORMAL,
                db=db,
            )

    def latest(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> Job | None:
        """The newest suggestion job of a Source System (owners and editors), or ``None``."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        self._services.systems.get(user, workspace_id, system_id)
        return self._services.jobs.latest(
            workspace_id, KPI_SUGGEST_JOB, {"source_system_id": str(system_id)}
        )

    # -- the job ------------------------------------------------------------------------

    def run(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``kpi_suggest`` job handler: the agent loop over the scoped tools, as the
        member who started it. Its result counts what was created, dropped and skipped."""
        workspace_id = uuid.UUID(str(params["workspace_id"]))
        system_id = uuid.UUID(str(params["source_system_id"]))
        user = self._users(uuid.UUID(str(params["user_id"])))
        if user is None or not user.is_active:
            raise RuntimeError("The member who started this job can no longer sign in.")
        try:
            self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
            self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
            system = self._services.systems.get(user, workspace_id, system_id)
            existing = self._services.kpis.every_state(user, workspace_id, system_id)
            gateway = self._roles.gateway_for_role(
                "agent", workspace_id=workspace_id, user_id=user.id
            )
        except ApiError as exc:
            raise RuntimeError(exc.message) from exc
        tables = self._tables(user, workspace_id, system_id)
        ctx.log(f"Started: {len(tables)} tables, {len(existing)} KPIs already documented.")
        bound = self._tools.bind(user, workspace_id, self._ai.policy(workspace_id), job=True)
        tools = ScopedKpiTools(bound, system_id)
        task = _task(system.name, tables, existing)
        finished = Finished("completed", "", [], 0, 0)
        calls = 0
        try:
            for event in run_agent(
                gateway,
                tools,
                system=SYSTEM_PROMPT + KPI_JOB_PROMPT + today_line(),
                history=[Message("user", task)],
                max_tool_calls=self._cap,
                is_cancelled=lambda: ctx.cancelled,
                timer=self._timer,
                delta_poll_seconds=STOP_POLL_SECONDS,
            ):
                if isinstance(event, ToolFinished):
                    calls += 1
                    ctx.log(f"{event.call.name} {event.call.status}")
                    ctx.progress(min(95, calls * 100 // self._cap))
                elif isinstance(event, Finished):
                    finished = event
                    break
        except ApiError as exc:
            finished = Finished("failed", "", [], 0, 0, exc.code, exc.message)
        if finished.status == "cancelled":
            raise JobCancelledError
        if finished.status == "failed":
            raise RuntimeError(
                redact_text(finished.error_message or "The assistant could not finish.")
            )
        ctx.log(
            f"Done: {tools.created} suggested, {tools.dropped} dropped (based on tables outside "
            f"the Source System), {tools.skipped} skipped (names already there)."
        )
        ctx.set_result(
            {"created": tools.created, "dropped": tools.dropped, "skipped": tools.skipped}
        )

    def _tables(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> list[str]:
        """The system's present tables as ``schema.table``; none before its first Snapshot."""
        try:
            content = self._services.snapshots.source_schema(user, workspace_id, system_id)
        except ApiError:
            return []
        return sorted(f"{t.db_schema}.{t.name}" for t in content.content.tables)


def _task(system: str, tables: Sequence[str], existing: Sequence[Any]) -> str:
    """The member's message to the job's model: the scope, its tables and every KPI already
    documented under it, quoted as data."""
    listed = "\n".join(tables[:MAX_LISTED_TABLES]) or "(no Snapshot yet)"
    if len(tables) > MAX_LISTED_TABLES:
        more = len(tables) - MAX_LISTED_TABLES
        listed += f"\n... and {more} more: find them with search_catalog."
    kpis = (
        "\n".join(
            f"- [{k.review_state}] {k.name}" + (f": {k.definition}" if k.definition else "")
            for k in existing
        )
        or "(none yet)"
    )
    return (
        "Suggest KPIs for the Source System named below, from its own data only.\n\n"
        + quote_data("source system", system)
        + "\n\nIts tables (schema.table):\n"
        + quote_data("source tables", listed)
        + "\n\nKPIs already documented under it, in every review state (live, suggested, "
        "accepted, rejected):\n" + quote_data("existing KPIs", kpis) + "\n\n" + KPI_SUGGESTION_RULES
    )
