"""Read tools of one turn run at the same time; writes follow, one by one (#407).
Driven through the real ``Gateway`` with the scripted ``FakeAdapter``."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from dawam.modules.assistant.internal.agent import Checkpoint, ToolOutcome, run_agent
from dawam.modules.assistant.internal.tools import BoundTools, Tool, ToolContext, ToolRegistry
from dawam.modules.llm import FakeAdapter, Gateway, Message, Reply, ToolCall, ToolSpec
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

SPECS = [ToolSpec(n, n, {"type": "object"}) for n in ("read_a", "read_b", "write_a", "write_b")]


class Tools:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = {"read": 0, "write": 0}
        self.peak = {"read": 0, "write": 0}
        self.spans: list[tuple[str, str, float, float]] = []
        self.barrier = threading.Barrier(2, timeout=5)
        self.b_done = threading.Event()
        self.extra: list[ToolSpec] = []

    def specs(self) -> Sequence[ToolSpec]:
        return [*SPECS, *self.extra]

    def is_read(self, name: str, arguments: dict[str, Any]) -> bool:
        return name.startswith("read")

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        kind = "read" if name.startswith("read") else "write"
        with self.lock:
            self.active[kind] += 1
            self.peak[kind] = max(self.peak[kind], self.active[kind])
        start = time.monotonic()
        if kind == "read":
            self.barrier.wait()  # only passes when both reads are inside at once
            if name == "read_a":
                self.b_done.wait(5)  # the second read finishes first
        else:
            time.sleep(0.02)
        end = time.monotonic()
        with self.lock:
            self.active[kind] -= 1
            self.spans.append((name, kind, start, end))
        if name == "read_b":
            self.b_done.set()
        return ToolOutcome(f"result of {name}", "ok")


def _drive(
    tools: Any, calls: tuple[ToolCall, ...], **options: Any
) -> tuple[list[Any], FakeAdapter]:
    adapter = FakeAdapter().script(Reply(tool_calls=calls), Reply(text="done"))
    gateway = Gateway(adapter, model="m", sleep=lambda _: None)
    events = list(run_agent(gateway, tools, system="S", history=[Message("user", "go")], **options))
    return events, adapter


def test_reads_overlap_writes_follow_one_by_one_and_results_keep_the_asked_order():
    tools = Tools()
    calls = (
        ToolCall("c1", "read_a", {}),
        ToolCall("c2", "write_a", {}),
        ToolCall("c3", "read_b", {}),
        ToolCall("c4", "write_b", {}),
    )

    events, adapter = _drive(tools, calls)

    assert events[-1].status == "completed"
    assert tools.peak == {"read": 2, "write": 1}
    ends = {n: e for n, k, _, e in tools.spans if k == "read"}
    starts = {n: s for n, k, s, _ in tools.spans if k == "write"}
    assert ends["read_b"] <= ends["read_a"]  # the later read finished first
    assert min(starts.values()) >= max(ends.values())  # writes after all reads
    # the reads' results in asked order, then the writes' in asked order
    told = [m.tool_call_id for m in adapter.calls[1].messages if m.role == "tool"]
    assert told == ["c1", "c3", "c2", "c4"]
    assert [c.id for c in events[-1].tool_calls] == ["c1", "c3", "c2", "c4"]


def test_the_checkpoint_is_saved_after_the_parallel_batch_not_between_its_reads():
    saved: list[Checkpoint] = []

    _drive(
        Tools(),
        (ToolCall("c1", "read_a", {}), ToolCall("c2", "read_b", {})),
        checkpoint=saved.append,
    )

    # after the model step, then once the whole batch is done
    assert [len(c.calls) for c in saved][:2] == [0, 2]
    assert saved[1].pending == ()


def test_find_tools_stays_sequential_after_the_reads():
    tools = Tools()
    tools.extra = [ToolSpec(f"t{i}", f"tool {i}", {"type": "object"}) for i in range(40)]

    events, _ = _drive(
        tools,
        (
            ToolCall("c1", "read_a", {}),
            ToolCall("c2", "find_tools", {"query": "tool 3"}),
            ToolCall("c3", "read_b", {}),
        ),
        core_tools={"read_a", "read_b"},
    )

    done = events[-1].tool_calls
    assert [c.id for c in done] == ["c1", "c3", "c2"]
    assert all(c.status == "ok" for c in done)
    assert tools.peak["read"] == 2


class _Plain:
    """A tool set without ``is_read``: every call runs in order."""

    def __init__(self) -> None:
        self.order: list[str] = []

    def specs(self) -> Sequence[ToolSpec]:
        return SPECS

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.order.append(name)
        return ToolOutcome("ok", "ok")


def test_a_tool_set_that_does_not_say_which_calls_are_reads_runs_in_order():
    tools = _Plain()

    _drive(tools, (ToolCall("c1", "write_a", {}), ToolCall("c2", "read_a", {})))

    assert tools.order == ["write_a", "read_a"]


def test_the_call_cap_skips_the_reads_over_it_without_running_them():
    tools = Tools()
    calls = (
        ToolCall("c1", "read_a", {}),
        ToolCall("c2", "read_b", {}),
        ToolCall("c3", "read_a", {}),
    )

    events, _ = _drive(tools, calls, max_tool_calls=2)

    statuses = {c.id: c.status for c in events[-1].tool_calls}
    assert statuses == {"c1": "ok", "c2": "ok", "c3": "skipped"}
    assert len(tools.spans) == 2


class _Args(BaseModel):
    n: int


class _Workspaces:
    def authorize(self, user: Any, action: Any, workspace_id: Any) -> None:
        if user == "denied":
            raise ApiError(403, "forbidden", "Not allowed.")


class _Registry:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def get(self, name: str) -> Any:
        return None if name == "missing" else type("T", (), {"kind": self.kind})()


def test_evaluate_dw_with_findings_is_a_write_without_findings_a_read():
    reads = BoundTools(_Registry("read"), None, None)  # type: ignore[arg-type]

    assert reads.is_read("evaluate_dw", {"layer": "silver"}) is True
    assert reads.is_read("evaluate_dw", {"layer": "silver", "findings": []}) is False
    assert reads.is_read("evaluate_dw", {"layer": "silver", "findings": [{"a": 1}]}) is False
    assert reads.is_read("missing", {}) is False
    assert BoundTools(_Registry("write"), None, None).is_read("x", {}) is False  # type: ignore[arg-type]


def test_unknown_tools_bad_arguments_and_refusals_never_run_the_body_and_say_why():
    ran: list[Any] = []
    tool = Tool("probe", "p", _Args, "read", Action.ASK_ASSISTANT, lambda ctx, a: ran.append(a))
    registry = ToolRegistry(_Workspaces(), None, [tool])  # type: ignore[arg-type]

    def bound(user: str) -> BoundTools:
        ctx = ToolContext(user, None, None, None)  # type: ignore[arg-type]
        return BoundTools(registry, ctx, None)  # type: ignore[arg-type]

    assert bound("ok").execute("nope", {}).status == "error"
    assert bound("ok").execute("probe", {"n": "x"}).status == "error"
    assert bound("denied").execute("probe", {"n": 1}).status == "refused"
    assert ran == []
    assert bound("ok").execute("probe", {"n": 1}).status == "ok" and len(ran) == 1
