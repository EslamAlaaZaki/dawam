"""The tool-selection evaluation set (AI enhancements §8): for a question and the page the
member is looking at, does the model pick the right tool, with the right key arguments?

Cases are data (``tool_selection/*.json``, each file a list), so adding one is not a code
change. A case holds the ``question``, the ``page_context`` (as the assistant quotes it), the
``expected`` tool and the arguments that must match (a subset: extra arguments are fine), and
the ``recorded`` answer a model once gave (a tool call, or plain ``text``). In CI the recorded
answers are replayed through the gateway by the scripted ``FakeAdapter``; no model is called.
To measure a real model, pass ``run`` a gateway for it and no recordings.

The report names the prompt version (``prompt_version`` of the system prompt and tool specs),
so scores of different prompt revisions can be compared.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dawam.modules.assistant import PageContext
from dawam.modules.assistant.internal.prompt import prompt_version
from dawam.modules.assistant.internal.tools import TOOLS
from dawam.modules.assistant.service import _system_prompt
from dawam.modules.llm import (
    FakeAdapter,
    Gateway,
    Message,
    Reply,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)

CASES = Path(__file__).parent / "tool_selection"


def cases() -> list[dict[str, Any]]:
    return [c for p in sorted(CASES.glob("*.json")) for c in json.loads(p.read_text("utf-8"))]


@dataclass(frozen=True)
class CaseResult:
    id: str
    language: str
    right_tool: bool
    right_arguments: bool
    chosen: str | None


@dataclass(frozen=True)
class Report:
    prompt_version: str
    results: tuple[CaseResult, ...]

    def _rate(self, pick: Callable[[CaseResult], bool], language: str | None = None) -> float:
        rows = [r for r in self.results if language in (None, r.language)]
        return round(sum(1 for r in rows if pick(r)) / len(rows), 3) if rows else 1.0

    @property
    def tool_score(self) -> float:
        return self._rate(lambda r: r.right_tool)

    @property
    def argument_score(self) -> float:
        return self._rate(lambda r: r.right_arguments)

    def by_language(self, language: str) -> float:
        return self._rate(lambda r: r.right_arguments, language)

    def summary(self) -> str:
        return (
            f"tool selection [{self.prompt_version}] {len(self.results)} cases: "
            f"right tool {self.tool_score}, right arguments {self.argument_score} "
            f"(en {self.by_language('en')}, ar {self.by_language('ar')})"
        )


def specs() -> list[ToolSpec]:
    return [t.spec for t in TOOLS]


def current_prompt_version() -> str:
    return prompt_version(_system_prompt(None), specs())


def _record(case: dict[str, Any]) -> Reply:
    recorded = case["recorded"]
    if "tool" in recorded:
        return Reply(tool_calls=(ToolCall("call_1", recorded["tool"], recorded["arguments"]),))
    return Reply(text=recorded["text"])


def run(
    case_list: Sequence[dict[str, Any]] | None = None,
    gateway: Gateway | None = None,
) -> Report:
    """Score ``case_list`` (default: all). Without a ``gateway``, each case's recorded answer
    is replayed through a gateway over the scripted ``FakeAdapter``."""
    offered = specs()
    results = []
    for case in cases() if case_list is None else case_list:
        model = gateway or Gateway(FakeAdapter().script(_record(case)), model="recorded")
        context = case.get("page_context")
        system = _system_prompt(PageContext(**context) if context else None)
        events = model.chat([Message("system", system), Message("user", case["question"])], offered)
        call = next((e.call for e in events if isinstance(e, ToolCallEvent)), None)
        want = case["expected"]
        right_tool = (call.name if call else None) == want["tool"]
        right_args = (
            right_tool
            and call is not None
            and all(call.arguments.get(k) == v for k, v in want["arguments"].items())
        ) or (right_tool and call is None)
        results.append(
            CaseResult(
                case["id"], case["language"], right_tool, right_args, call.name if call else None
            )
        )
    return Report(current_prompt_version(), tuple(results))
