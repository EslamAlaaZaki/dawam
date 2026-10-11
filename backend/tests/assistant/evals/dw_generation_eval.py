"""The DW generation evaluation set (spec §6.9, story 88): sample sources with a reference
Kimball model each, and a score of how close a generated model comes to the reference.

Each case in ``dw_generation/`` holds the ``inputs`` the ``generate_dw_schema`` tool gives the
model (the same shape ``GenerationService.inputs`` returns, plus the KPIs) and the
``reference`` tables, in the tool's own argument shape. ``score`` matches tables by kind and
normalised name and averages what a reviewer checks first: the facts and dimensions found,
facts with a grain, the dimensions' SCD types, the measures' additivity and the star's foreign
keys. It is a yardstick for comparing models and prompts before a release, not a test of
exact equality: a good model scores well without naming everything the same way.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CASES = Path(__file__).parent / "dw_generation"
_PREFIX = re.compile(r"^(dim|fact|fct|d|f|bridge)_")


def cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))]


def _norm(name: str) -> str:
    name = _PREFIX.sub("", name.lower())
    return name[:-1] if name.endswith("s") and len(name) > 3 else name


def _tokens(name: str) -> set[str]:
    return {_norm(t) for t in _norm(name).split("_") if t}


def _same(a: str, b: str) -> bool:
    if _norm(a) == _norm(b):
        return True
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and (ta <= tb or tb <= ta)


@dataclass(frozen=True)
class Score:
    tables_found: float
    facts_with_grain: float
    scd_types: float
    additivity: float
    foreign_keys: float

    @property
    def overall(self) -> float:
        parts = (
            self.tables_found,
            self.facts_with_grain,
            self.scd_types,
            self.additivity,
            self.foreign_keys,
        )
        return round(sum(parts) / len(parts), 3)


def _ratio(hits: int, total: int) -> float:
    return 1.0 if total == 0 else hits / total


def _match(
    reference: list[dict[str, Any]], generated: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Each reference table's best counterpart of the same kind (each used once)."""
    used: set[int] = set()
    out: dict[str, dict[str, Any]] = {}
    for ref in reference:
        for exact in (True, False):
            hit = next(
                (
                    i
                    for i, t in enumerate(generated)
                    if i not in used
                    and t.get("kind") == ref["kind"]
                    and (
                        _norm(t["name"]) == _norm(ref["name"])
                        if exact
                        else _same(t["name"], ref["name"])
                    )
                ),
                None,
            )
            if hit is not None:
                used.add(hit)
                out[ref["name"]] = generated[hit]
                break
    return out


def _referenced(column: dict[str, Any], names: dict[str, str]) -> str | None:
    """The reference name of the table a foreign key points at (``date`` for the date one)."""
    target = str(column.get("references") or "")
    if _norm(target) == "date":  # `date`, or the date dimension by name
        return "date"
    return names.get(target.lower())


def score(reference: list[dict[str, Any]], generated: list[dict[str, Any]]) -> Score:
    matched = _match(reference, generated)
    names = {t["name"].lower(): ref for ref, t in matched.items()}
    facts = [t for t in generated if t.get("kind") == "fact"]
    scd = additivity = additivity_total = fks = fks_total = 0
    dims = [r for r in reference if r["kind"] == "dimension"]
    for ref in reference:
        mine = matched.get(ref["name"])
        if ref["kind"] == "dimension":
            scd += bool(mine) and (mine.get("scd_type") or 1) == ref.get("scd_type", 1)
            continue
        columns = {c["name"]: c for c in (mine or {}).get("columns", [])}
        for column in ref["columns"]:
            if column["role"] == "measure":
                additivity_total += 1
                found = next(
                    (
                        c
                        for n, c in columns.items()
                        if c["role"] == "measure" and _same(n, column["name"])
                    ),
                    None,
                )
                additivity += bool(found) and found.get("additivity") == column["additivity"]
            if column["role"] == "fk":
                fks_total += 1
                want = "date" if column["references"] == "date" else column["references"]
                fks += any(
                    c["role"] == "fk" and _referenced(c, names) == want for c in columns.values()
                )
    return Score(
        tables_found=_ratio(len(matched), len(reference)),
        facts_with_grain=_ratio(
            sum(1 for f in facts if (f.get("grain") or "").strip()), len(facts)
        ),
        scd_types=_ratio(scd, len(dims)),
        additivity=_ratio(additivity, additivity_total),
        foreign_keys=_ratio(fks, fks_total),
    )
