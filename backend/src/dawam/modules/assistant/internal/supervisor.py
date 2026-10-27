"""A job run by a supervisor and specialists (ADR 0009), behind ``assistant_job_supervisor``.

The supervisor is ``run_agent`` over a ``Supervisor`` tool set: it plans (``write_plan``,
``update_plan``), asks the member (``ask_member``) and hands parts of the task to a
specialist with ``delegate``. A delegated task is another ``run_agent`` over the same gateway
and the same bound tools, narrowed to the specialist's few tools, with a context of its own:
its system prompt and the task, nothing of the supervisor's turns. Its report comes back as
the ``delegate`` result, quoted as data like any tool result.

What the loop gives a job holds across both levels: every call still runs as the member
through ``BoundTools.execute``; the specialists' calls count against the job's one cap
(``spent``); and the specialist in progress is checkpointed with the supervisor, so a job
whose worker died resumes inside it. Self-verification runs on the supervisor's run, which
checks the job's proposals as a whole. A specialist does not ask the member: it says what it
needs in its report and the supervisor asks.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dawam.modules.llm import Message, ToolSpec

from .agent import (
    ASK_MEMBER,
    LIMIT_MESSAGE,
    VERIFY_TOOLS,
    ChatGateway,
    Checkpoint,
    Finished,
    ToolFinished,
    ToolOutcome,
    _counted,
    run_agent,
)

DELEGATE = "delegate"
PLAN_TOOLS = frozenset({"write_plan", "update_plan"})
SUPERVISOR_TOOLS = PLAN_TOOLS | {ASK_MEMBER, *VERIFY_TOOLS}
"""The supervisor's own tools besides ``delegate``; the checks are for self-verification."""

_LOOKUP = frozenset({"search_catalog", "get_object", "workspace_overview", "search_workspace"})


@dataclass(frozen=True)
class Specialist:
    name: str
    role: str
    """What it is for: shown to the supervisor and opening the specialist's own prompt."""
    tools: frozenset[str]


SPECIALISTS: Mapping[str, Specialist] = {
    s.name: s
    for s in (
        Specialist(
            "source_analyst",
            "the source analyst: reads the source systems (tables, profiles, relationships, "
            "PII, lineage, documents, files, source queries) and proposes descriptions of "
            "source objects",
            _LOOKUP
            | {
                "get_profile",
                "get_snapshot_diff",
                "get_relationships",
                "get_pii_findings",
                "get_lineage",
                "explore_graph",
                "search_documents",
                "list_files",
                "read_file",
                "query_file",
                "run_source_query",
                "propose_changes",
            },
        ),
        Specialist(
            "dw_modeler",
            "the DW modeler: designs and reviews the Data Warehouse model (facts, dimensions, "
            "checks and score) and generates DW schema",
            _LOOKUP
            | {
                "get_relationships",
                "get_lineage",
                "explore_graph",
                "get_score",
                "explain_check",
                "propose_check_fix",
                "evaluate_dw",
                "run_validation",
                "generate_dw_schema",
            },
        ),
        Specialist(
            "mapper",
            "the mapper: drafts the source-to-DW mappings of DW tables",
            _LOOKUP
            | {
                "get_profile",
                "get_relationships",
                "get_lineage",
                "explore_graph",
                "run_validation",
                "draft_mappings",
            },
        ),
        Specialist(
            "kpi_analyst",
            "the KPI analyst: reads and suggests KPIs and links them to the DW",
            _LOOKUP
            | {
                "get_lineage",
                "search_documents",
                "suggest_kpis",
                "get_kpi_link_candidates",
                "propose_kpi_links",
            },
        ),
    )
}

DELEGATE_SPEC = ToolSpec(
    DELEGATE,
    "Hand one part of the task to a specialist, who works it out with its own tools and "
    "reports back. Give the complete sub-task: the specialist sees nothing else. "
    + " ".join(f"`{s.name}` is {s.role}." for s in SPECIALISTS.values()),
    {
        "type": "object",
        "properties": {
            "specialist": {"type": "string", "enum": list(SPECIALISTS)},
            "task": {"type": "string", "description": "The sub-task, complete."},
        },
        "required": ["specialist", "task"],
    },
)

SUPERVISOR_PROMPT = """\


You are running as a background job, as the supervisor of a team of specialists: the \
member handed you one long task and is not watching each step. Start by calling write_plan \
with your steps and keep it current with update_plan. Do the work by handing each step to \
the right specialist with `delegate`; you have no other tools to read or change the \
Workspace. Finish with a short summary of what was found and done. Changes the specialists \
propose are collected into a single Change Set that the member reviews when you finish. If \
you cannot go on without a decision only the member can make, call `ask_member` with one \
clear question: the job pauses until they answer.\
"""

SPECIALIST_PROMPT = """\


You are {role}. A supervisor handed you one part of a background job's task. Work it out \
with your tools, then answer with a short report of what you found and did. Changes you \
propose are collected for the member to review; nothing is changed until then. If you need \
a decision only the member can make, say so in your report instead of guessing.\
"""


def _key(arguments: Mapping[str, Any]) -> dict[str, str]:
    """Which delegated task a call is, to find its checkpoint again."""
    return {
        "specialist": str(arguments.get("specialist", "")),
        "task": str(arguments.get("task", "")).strip(),
    }


class _Narrowed:
    """The bound tools as a specialist sees them: only its ``names`` are offered or run."""

    def __init__(self, tools: Any, names: frozenset[str]) -> None:
        self._tools = tools
        self._names = names

    def specs(self) -> Sequence[ToolSpec]:
        return [s for s in self._tools.specs() if s.name in self._names]

    def is_read(self, name: str, arguments: dict[str, Any]) -> bool:
        return name in self._names and bool(self._tools.is_read(name, arguments))

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name not in self._names:
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        return self._tools.execute(name, arguments)  # as the member, authorised again


class Supervisor:
    """The ``ToolSet`` of the supervisor's run over a job's bound ``tools``.

    ``saved`` is what ``saved()`` returned at the last checkpoint (resuming); ``on_save`` is
    called when the specialist in progress reached a checkpoint, so the job stores it with
    the rest; ``on_event`` gets the specialist's ``ToolFinished`` reports."""

    def __init__(
        self,
        tools: Any,
        gateway: ChatGateway,
        *,
        system: str,
        max_tool_calls: int,
        saved: Mapping[str, Any] | None = None,
        on_save: Callable[[], None] = lambda: None,
        on_event: Callable[[ToolFinished], None] = lambda _: None,
        is_cancelled: Callable[[], bool] = lambda: False,
        run: Callable[..., Any] = run_agent,
        **loop: Any,
    ) -> None:
        self._tools = tools
        self._gateway = gateway
        self._system = system
        self._cap = max_tool_calls
        self._on_save = on_save
        self._on_event = on_event
        self._is_cancelled = is_cancelled
        self._run = run
        self._loop = loop
        self._lock = threading.Lock()
        self._spent: int = int(saved.get("spent", 0)) if saved else 0
        """Calls of the job so far that count against its cap, at both levels."""
        child = saved.get("child") if saved else None
        self._child: dict[str, Any] | None = dict(child) if child else None
        self.prompt_tokens: int = int(saved.get("prompt_tokens", 0)) if saved else 0
        """What the finished specialists used (the one in progress keeps its own count)."""
        self.completion_tokens: int = int(saved.get("completion_tokens", 0)) if saved else 0

    def saved(self) -> dict[str, Any]:
        return {
            "spent": self._spent,
            "child": self._child,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }

    def specs(self) -> Sequence[ToolSpec]:
        own = [s for s in self._tools.specs() if s.name in SUPERVISOR_TOOLS]
        return sorted([DELEGATE_SPEC, *own], key=lambda spec: spec.name)

    def is_read(self, name: str, arguments: dict[str, Any]) -> bool:
        return name in SUPERVISOR_TOOLS and bool(self._tools.is_read(name, arguments))

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name not in SUPERVISOR_TOOLS and name != DELEGATE:
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        resuming = name == DELEGATE and self._resumes(arguments)  # counted before it died
        if name not in VERIFY_TOOLS and not resuming:  # the loop's own checks do not count
            with self._lock:
                if self._spent >= self._cap:
                    return ToolOutcome(LIMIT_MESSAGE, "skipped")
                self._spent += 1
        if name == DELEGATE:
            return self._delegate(arguments)
        return self._tools.execute(name, arguments)

    def _resumes(self, arguments: dict[str, Any]) -> bool:
        return self._child is not None and self._child.get("key") == _key(arguments)

    def _delegate(self, arguments: dict[str, Any]) -> ToolOutcome:
        specialist = SPECIALISTS.get(str(arguments.get("specialist", "")))
        task = str(arguments.get("task", "")).strip()
        if specialist is None or not task:
            names = ", ".join(SPECIALISTS)
            return ToolOutcome(
                f"Invalid arguments for `{DELEGATE}`: specialist: one of {names}; task: "
                "a non-empty string is required",
                "error",
            )
        key = _key(arguments)
        resume: Checkpoint | None = None
        start = self._spent
        if self._child is not None and self._child.get("key") == key:  # the worker died in it
            done = self._child.get("done")
            if done is not None:  # after it finished: its report stands
                return ToolOutcome(done["content"], done["status"], done["saved"])
            start = int(self._child["start"])
            resume = Checkpoint.from_json(self._child.get("run") or {})
        self._child = {"key": key, "start": start, "run": None}
        child = self._child

        def save(state: Checkpoint) -> None:
            child["run"] = state.to_json()
            self._spent = start + _counted(state.calls)
            self._on_save()

        finished = Finished("failed", "", [], 0, 0, "internal_error", "The specialist stopped.")
        for event in self._run(
            self._gateway,
            _Narrowed(self._tools, specialist.tools),
            system=self._system + SPECIALIST_PROMPT.format(role=specialist.role),
            history=[Message("user", task)],
            max_tool_calls=max(0, self._cap - start),
            is_cancelled=self._is_cancelled,
            checkpoint=save,
            resume=resume,
            **self._loop,
        ):
            if isinstance(event, ToolFinished):
                self._on_event(event)
            elif isinstance(event, Finished):
                finished = event
                break
        self._spent = start + _counted(finished.tool_calls)
        self.prompt_tokens += finished.prompt_tokens
        self.completion_tokens += finished.completion_tokens
        outcome = _outcome(specialist, finished)
        # Kept until the supervisor's own checkpoint has the result (``settle``), so a worker
        # that dies in between hands back this report instead of running the specialist again.
        self._child = {
            "key": key,
            "start": start,
            "done": {"content": outcome.content, "status": outcome.status, "saved": outcome.saved},
        }
        self._on_save()
        return outcome

    def settle(self) -> None:
        """The supervisor's run checkpointed: a finished specialist's report is in it."""
        if self._child is not None and "done" in self._child:
            self._child = None


def _outcome(specialist: Specialist, finished: Finished) -> ToolOutcome:
    saved = {
        "specialist": specialist.name,
        "status": finished.status,
        "tool_calls": _counted(finished.tool_calls),
    }
    if finished.status == "cancelled":
        return ToolOutcome("The job was cancelled.", "error", saved)
    if finished.status == "failed":
        reason = finished.error_message or "The specialist could not finish."
        return ToolOutcome(f"The specialist failed: {reason}", "error", saved)
    report = finished.text.strip() or "The specialist reported nothing."
    if finished.status == "tool_limit":
        report += "\n\n(The job's tool-call limit was reached.)"
    return ToolOutcome(report, "ok", saved)
