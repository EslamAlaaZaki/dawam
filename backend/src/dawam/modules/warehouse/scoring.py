"""The scoring engine (spec §6.11): rule-based, deterministic, and pure.

A ``Design`` (plain data about the DW Schema and its mappings, no database) goes in; a
``ScoreReport`` comes out. Checks are registered on a ``CheckRegistry`` and the engine
knows nothing of any particular check, so adding one means writing a function:

    @registry.table_check("fact_has_grain", category="completeness", severity="error",
                          layers=("core", "mart"), kinds=("fact",), hint="Write the grain.")
    def fact_has_grain(table, design):
        return PASS if table.grain else fail("The fact has no grain statement.")

A check function returns ``None`` when it does not apply to the object, ``PASS``, or
``fail(message)``. ``column_check`` does the same per column; ``check`` is the general form
that yields ``Finding`` objects itself (for checks that look across the whole design).

Formula (spec §6.11): severity weights error 10, warning 3, info 1. A table's score is the
weighted pass percentage of the findings about it (its columns' included); a Layer's score is
the average of its tables' scores weighted by column count; the Data Warehouse score is the
weighted pass percentage over the Core and Mart findings. Staging is scored on its own and
never counts towards the Data Warehouse. Nothing to score is ``None`` ("not scored"), never
0 or 100. An unresolved error caps a grade at C.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]
Layer = Literal["staging", "core", "mart"]
Category = Literal[
    "completeness",
    "dimensional_modeling",
    "consistency",
    "traceability",
    "documentation",
    "performance_readiness",
    "privacy",
]
Grade = Literal["A", "B", "C", "D", "F"]

SEVERITY_WEIGHTS: Mapping[str, int] = {"error": 10, "warning": 3, "info": 1}
GRADES: tuple[tuple[float, Grade], ...] = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))
ERROR_CAP: Grade = "C"
DW_LAYERS: tuple[str, ...] = ("core", "mart")
"""The Layers that make up the Data Warehouse score."""
ALL_LAYERS: tuple[str, ...] = ("staging", "core", "mart")


def grade_of(score: float, *, has_errors: bool = False) -> Grade:
    """The letter grade of a score; an unresolved error caps it at C."""
    grade: Grade = next((g for floor, g in GRADES if score >= floor), "F")
    if has_errors and grade in ("A", "B"):
        return ERROR_CAP
    return grade


# --- the design the checks read ------------------------------------------------------


@dataclass(frozen=True)
class MappingFact:
    """One saved column mapping: table-level (``branch_id`` None) or of a branch."""

    branch_id: uuid.UUID | None
    mapping_type: str
    sql_expression: str = ""


@dataclass(frozen=True)
class DesignBranch:
    id: uuid.UUID
    name: str
    driving_input: str
    joins: str = ""
    filters: str = ""
    group_by: str | None = None
    having: str | None = None


@dataclass(frozen=True)
class DesignColumn:
    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    data_type: Mapping[str, Any]
    role: str
    additivity: str | None = None
    references_table_id: uuid.UUID | None = None
    is_system: bool = False
    """DAWAM maintains it; naming rules do not apply."""
    needs_no_mapping: bool = False
    """A system column (surrogate key, SCD housekeeping, audit): always covered."""
    mappings: tuple[MappingFact, ...] = ()


@dataclass(frozen=True)
class DesignTable:
    id: uuid.UUID
    layer: str
    name: str
    kind: str
    fact_type: str | None = None
    grain: str | None = None
    is_aggregate: bool = False
    scd_type: int | None = None
    is_conformed: bool = False
    description: str = ""
    columns: tuple[DesignColumn, ...] = ()
    branches: tuple[DesignBranch, ...] = ()


@dataclass(frozen=True)
class Design:
    """The model as the checks see it: every table that is scored (a deleted object never is)."""

    platform: str
    naming_rules: Any
    """A ``NamingRulesLike`` (see ``naming``)."""
    tables: tuple[DesignTable, ...] = ()
    by_id: dict[uuid.UUID, DesignTable] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_id", {t.id: t for t in self.tables})

    def table(self, table_id: uuid.UUID | None) -> DesignTable | None:
        return self.by_id.get(table_id) if table_id else None


# --- findings and checks -------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    passed: bool
    message: str = ""
    severity: Severity | None = None
    """Overrides the check's own severity for this object (a check may be an error in one
    Layer and a warning in another)."""


PASS = Outcome(True)


def fail(message: str, *, severity: Severity | None = None) -> Outcome:
    return Outcome(False, message, severity)


@dataclass(frozen=True)
class CheckSpec:
    code: str
    category: Category
    severity: Severity
    layers: tuple[str, ...]
    title: str
    hint: str
    """How to fix a failure; shown with every failed result."""


@dataclass(frozen=True)
class Finding:
    """The result of one check on one object."""

    code: str
    severity: Severity
    layer: str
    object_type: Literal["table", "column"]
    object_id: uuid.UUID
    table_id: uuid.UUID
    object_name: str
    passed: bool
    message: str = ""


CheckFunction = Callable[[Design, CheckSpec], Iterable[Finding]]


def _finding(
    spec: CheckSpec, outcome: Outcome, table: DesignTable, column: DesignColumn | None
) -> Finding:
    return Finding(
        code=spec.code,
        severity=outcome.severity or spec.severity,
        layer=table.layer,
        object_type="table" if column is None else "column",
        object_id=table.id if column is None else column.id,
        table_id=table.id,
        object_name=table.name if column is None else f"{table.name}.{column.name}",
        passed=outcome.passed,
        message=outcome.message,
    )


class CheckRegistry:
    """The checks, in registration order, by code."""

    def __init__(self) -> None:
        self._checks: dict[str, tuple[CheckSpec, CheckFunction]] = {}

    @property
    def specs(self) -> tuple[CheckSpec, ...]:
        return tuple(spec for spec, _ in self._checks.values())

    def spec(self, code: str) -> CheckSpec | None:
        entry = self._checks.get(code)
        return entry[0] if entry else None

    def check(
        self,
        code: str,
        *,
        category: Category,
        severity: Severity,
        layers: Collection[str],
        hint: str,
        title: str | None = None,
    ) -> Callable[[CheckFunction], CheckFunction]:
        """Register a check that yields its own findings (for the whole design)."""

        def register(function: CheckFunction) -> CheckFunction:
            if code in self._checks:
                raise ValueError(f"The check {code!r} is already registered.")
            spec = CheckSpec(
                code, category, severity, tuple(layers), title or (function.__doc__ or code), hint
            )
            self._checks[code] = (spec, function)
            return function

        return register

    def table_check(
        self,
        code: str,
        *,
        category: Category,
        severity: Severity,
        layers: Collection[str],
        hint: str,
        kinds: Collection[str] | None = None,
        title: str | None = None,
    ) -> Callable[[Callable[[DesignTable, Design], Outcome | None]], Any]:
        """Register a check run on each table of ``layers`` (and of ``kinds``, when given)."""

        def register(function: Callable[[DesignTable, Design], Outcome | None]) -> Any:
            def run(design: Design, spec: CheckSpec) -> Iterator[Finding]:
                for table in design.tables:
                    if table.layer not in spec.layers or (kinds and table.kind not in kinds):
                        continue
                    outcome = function(table, design)
                    if outcome is not None:
                        yield _finding(spec, outcome, table, None)

            self.check(
                code,
                category=category,
                severity=severity,
                layers=layers,
                hint=hint,
                title=title or function.__doc__,
            )(run)
            return function

        return register

    def column_check(
        self,
        code: str,
        *,
        category: Category,
        severity: Severity,
        layers: Collection[str],
        hint: str,
        kinds: Collection[str] | None = None,
        title: str | None = None,
    ) -> Callable[[Callable[[DesignColumn, DesignTable, Design], Outcome | None]], Any]:
        """Register a check run on each column of the tables of ``layers`` (and ``kinds``)."""

        def register(
            function: Callable[[DesignColumn, DesignTable, Design], Outcome | None],
        ) -> Any:
            def run(design: Design, spec: CheckSpec) -> Iterator[Finding]:
                for table in design.tables:
                    if table.layer not in spec.layers or (kinds and table.kind not in kinds):
                        continue
                    for column in table.columns:
                        outcome = function(column, table, design)
                        if outcome is not None:
                            yield _finding(spec, outcome, table, column)

            self.check(
                code,
                category=category,
                severity=severity,
                layers=layers,
                hint=hint,
                title=title or function.__doc__,
            )(run)
            return function

        return register

    def run(self, design: Design, *, disabled: Collection[str] = ()) -> list[Finding]:
        """Every finding of the enabled checks, in check order; one that names an object
        outside the check's Layers is dropped, so a check cannot score what it does not cover."""
        findings: list[Finding] = []
        for code, (spec, function) in self._checks.items():
            if code in disabled:
                continue
            findings.extend(f for f in function(design, spec) if f.layer in spec.layers)
        return findings


# --- the formula ---------------------------------------------------------------------


@dataclass(frozen=True)
class TableScore:
    table_id: uuid.UUID
    layer: str
    name: str
    score: float | None
    """``None``: no check applies to the table."""
    column_count: int


@dataclass(frozen=True)
class LayerScore:
    layer: str
    score: float | None
    """``None``: "not scored" (no table, or no check applies)."""
    grade: Grade | None
    table_count: int


@dataclass(frozen=True)
class ScoreReport:
    score: float | None
    """The Data Warehouse score (Core and Mart); ``None`` when there is nothing to score."""
    grade: Grade | None
    capped: bool
    """An unresolved error held the grade at C."""
    layers: tuple[LayerScore, ...]
    tables: tuple[TableScore, ...]
    findings: tuple[Finding, ...]
    disabled: tuple[str, ...] = ()

    def layer(self, name: str) -> LayerScore:
        return next(item for item in self.layers if item.layer == name)

    @property
    def failed(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]


def _percentage(findings: Iterable[Finding]) -> float | None:
    total = passed = 0
    for f in findings:
        weight = SEVERITY_WEIGHTS[f.severity]
        total += weight
        passed += weight if f.passed else 0
    return None if total == 0 else round(100 * passed / total, 2)


def _has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity == "error" and not f.passed for f in findings)


def _scored(score: float | None, findings: list[Finding]) -> tuple[Grade | None, bool]:
    if score is None:
        return None, False
    plain, capped = grade_of(score), grade_of(score, has_errors=_has_errors(findings))
    return capped, capped != plain


def evaluate(
    design: Design, registry: CheckRegistry, *, disabled: Collection[str] = ()
) -> ScoreReport:
    """Run the enabled checks of ``registry`` on ``design`` and score the result."""
    findings = registry.run(design, disabled=disabled)
    by_table: dict[uuid.UUID, list[Finding]] = {}
    for f in findings:
        by_table.setdefault(f.table_id, []).append(f)
    tables = tuple(
        TableScore(t.id, t.layer, t.name, _percentage(by_table.get(t.id, [])), len(t.columns))
        for t in design.tables
    )
    layers = []
    for name in ALL_LAYERS:
        scored = [t for t in tables if t.layer == name and t.score is not None]
        weight = sum(max(t.column_count, 1) for t in scored)
        score = (
            round(
                sum(t.score * max(t.column_count, 1) for t in scored if t.score is not None)
                / weight,
                2,
            )
            if weight
            else None
        )
        layer_findings = [f for f in findings if f.layer == name]
        grade, _ = _scored(score, layer_findings)
        layers.append(
            LayerScore(name, score, grade, sum(1 for t in design.tables if t.layer == name))
        )
    dw_findings = [f for f in findings if f.layer in DW_LAYERS]
    score = _percentage(dw_findings)
    grade, capped = _scored(score, dw_findings)
    return ScoreReport(
        score=score,
        grade=grade,
        capped=capped,
        layers=tuple(layers),
        tables=tables,
        findings=tuple(findings),
        disabled=tuple(sorted(disabled)),
    )
