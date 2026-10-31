"""The ``find_tools`` retrieval set (#528): given what the model asks for, does
``search_tools`` put the right hidden tool in the top 5 (and, where clear, first)?

Cases are data (``find_tools/*.json``): a ``query``, the ``expected`` tools that must be in the
top 5, and ``top1``, the tool that must come first (``null`` when no single one is clear).
They are grouped (``paraphrase``, ``common_words``, ``exact_name``, ``exact_names``) so a
scoring change shows where it helps. It runs ``search_tools`` over the real ``TOOLS`` specs:
pure Python, no model call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dawam.modules.assistant.internal.agent import search_tools
from dawam.modules.assistant.internal.tools import TOOLS

CASES = Path(__file__).parent / "find_tools"


def cases() -> list[dict[str, Any]]:
    return [c for p in sorted(CASES.glob("*.json")) for c in json.loads(p.read_text("utf-8"))]


@dataclass(frozen=True)
class CaseResult:
    id: str
    group: str
    in_top5: bool
    first: bool | None  # None: the case names no single tool that must come first
    found: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    results: tuple[CaseResult, ...]

    def top5(self, group: str | None = None) -> float:
        rows = [r for r in self.results if group in (None, r.group)]
        return round(sum(r.in_top5 for r in rows) / len(rows), 3) if rows else 1.0

    def top1(self, group: str | None = None) -> float:
        rows = [r for r in self.results if r.first is not None and group in (None, r.group)]
        return round(sum(bool(r.first) for r in rows) / len(rows), 3) if rows else 1.0

    def summary(self) -> str:
        groups = sorted({r.group for r in self.results})
        per = ", ".join(f"{g} {self.top1(g)}/{self.top5(g)}" for g in groups)
        return (
            f"find_tools {len(self.results)} cases: top-1 {self.top1()}, top-5 {self.top5()} "
            f"({per})"
        )


def run() -> Report:
    specs = [t.spec for t in TOOLS]
    results = []
    for case in cases():
        found = tuple(s.name for s in search_tools(case["query"], specs))
        top1 = case.get("top1")
        results.append(
            CaseResult(
                case["id"],
                case["group"],
                set(case["expected"]) <= set(found),
                None if top1 is None else bool(found) and found[0] == top1,
                found,
            )
        )
    return Report(tuple(results))
