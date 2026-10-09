"""The registered quality checks (spec §6.11), on the default registry.

Each check is a small function over the ``Design`` snapshot; the engine (``scoring``) runs
them and scores the result. A later check is one more decorated function here (or in a module
this one imports): it needs no change to the engine.
"""

from __future__ import annotations

from .calendar import DATE_KEY
from .lineage_sql import Unparsable, is_group_safe, parse_branch
from .naming import check_column_name, check_table_name
from .scoring import (
    PASS,
    CheckRegistry,
    Design,
    DesignColumn,
    DesignTable,
    Outcome,
    fail,
)

MODEL = ("core", "mart")
DIMENSIONAL = "dimensional_modeling"
REFERENCED_BY_FACTS = ("dimension", "generated")

registry = CheckRegistry()
"""The checks the product scores with."""

_COVERED = ("direct", "derived", "constant", "lookup", "system", "not_in_branch")
_TIME_TYPES = ("date", "time", "timestamp", "timestamptz")


def _is_generated(table: DesignTable) -> bool:
    return table.kind == "generated"


# --- completeness --------------------------------------------------------------------


@registry.table_check(
    "fact_has_grain",
    category="completeness",
    severity="error",
    layers=MODEL,
    kinds=("fact",),
    hint="Write the grain statement: what one row of the fact stands for.",
    title="Every fact has a grain statement",
)
def fact_has_grain(table: DesignTable, design: Design) -> Outcome:
    if (table.grain or "").strip():
        return PASS
    return fail(f"{table.name} has no grain statement.")


@registry.column_check(
    "column_covered",
    category="completeness",
    severity="warning",
    layers=MODEL,
    hint="Map the column in every branch, or mark it 'not available in this branch'.",
    title="Every column is covered in every branch",
)
def column_covered(column: DesignColumn, table: DesignTable, design: Design) -> Outcome | None:
    if _is_generated(table):
        return None
    if column.needs_no_mapping:
        return PASS
    if table.branches:
        by_branch = {m.branch_id: m for m in column.mappings}
        gaps = [
            b.name
            for b in table.branches
            if (m := by_branch.get(b.id)) is None or m.mapping_type not in _COVERED
        ]
        if gaps:
            return fail(f"{column.name} is not mapped in branch {', '.join(gaps)}.")
        return PASS
    table_level = [m for m in column.mappings if m.branch_id is None]
    if table_level and table_level[0].mapping_type in _COVERED:
        return PASS
    return fail(f"{column.name} is not mapped.")


@registry.table_check(
    "aggregate_group_by",
    category="completeness",
    severity="error",
    layers=MODEL,
    kinds=("fact",),
    hint="Aggregate the column, or add it to the branch's GROUP BY.",
    title="Every aggregate table's non-aggregated columns appear in its GROUP BY",
)
def aggregate_group_by(table: DesignTable, design: Design) -> Outcome | None:
    if not table.is_aggregate:
        return None
    gaps: list[str] = []
    for branch in table.branches:
        try:
            parts = parse_branch(
                branch.driving_input,
                branch.joins,
                branch.filters,
                branch.group_by,
                branch.having,
                design.platform,
            )
        except Unparsable:
            continue  # reported by the mapping-parses check
        for column in table.columns:
            for m in column.mappings:
                if (
                    m.branch_id == branch.id
                    and m.mapping_type in ("direct", "derived")
                    and not is_group_safe(m.sql_expression, parts, design.platform)
                ):
                    gaps.append(f"{column.name} (branch {branch.name})")
    if gaps:
        return fail(f"Not aggregated and not in the GROUP BY: {', '.join(gaps)}.")
    return PASS


# --- dimensional modeling ------------------------------------------------------------


@registry.table_check(
    "dimension_has_keys",
    category=DIMENSIONAL,
    severity="error",
    layers=MODEL,
    kinds=("dimension",),
    hint="Give the dimension a surrogate key (role sk) and a natural key (role nk).",
    title="Every dimension has a surrogate key and a natural key",
)
def dimension_has_keys(table: DesignTable, design: Design) -> Outcome:
    roles = {c.role for c in table.columns}
    missing = [
        label for role, label in (("sk", "surrogate"), ("nk", "natural")) if role not in roles
    ]
    if missing:
        return fail(f"{table.name} has no {' and no '.join(missing)} key.")
    return PASS


def _foreign_keys(table: DesignTable, design: Design) -> list[DesignTable]:
    """The tables the table's foreign keys point at (the ones that are still scored)."""
    return [
        target
        for c in table.columns
        if c.role == "fk" and (target := design.table(c.references_table_id)) is not None
    ]


@registry.table_check(
    "fact_has_dimension_fk",
    category=DIMENSIONAL,
    severity="error",
    layers=MODEL,
    kinds=("fact",),
    hint="Add a foreign key column that references a dimension.",
    title="Every fact has at least one dimension foreign key",
)
def fact_has_dimension_fk(table: DesignTable, design: Design) -> Outcome:
    if any(t.kind in REFERENCED_BY_FACTS for t in _foreign_keys(table, design)):
        return PASS
    return fail(f"{table.name} has no foreign key to a dimension.")


@registry.column_check(
    "no_fact_to_fact_fk",
    category=DIMENSIONAL,
    severity="error",
    layers=MODEL,
    kinds=("fact",),
    hint="Reference a shared dimension instead, or merge the two facts' grain.",
    title="No fact-to-fact foreign keys",
)
def no_fact_to_fact_fk(column: DesignColumn, table: DesignTable, design: Design) -> Outcome | None:
    if column.role != "fk":
        return None
    target = design.table(column.references_table_id)
    if target is not None and target.kind == "fact":
        return fail(f"{column.name} references the fact {target.name}.")
    return PASS


def _is_date_dimension(table: DesignTable) -> bool:
    return (
        table.kind == "generated"
        or table.name.lower() == "dim_date"
        or any(c.name == DATE_KEY for c in table.columns)
    )


def _time_based(table: DesignTable) -> bool:
    return table.fact_type in ("periodic_snapshot", "accumulating_snapshot") or any(
        c.role in ("measure", "attribute") and c.data_type.get("type") in _TIME_TYPES
        for c in table.columns
    )


@registry.table_check(
    "time_fact_has_date_dimension",
    category=DIMENSIONAL,
    severity="warning",
    layers=MODEL,
    kinds=("fact",),
    hint="Add a foreign key to the date dimension (add the generated one if there is none).",
    title="Facts with time-based measures link to a date dimension",
)
def time_fact_has_date_dimension(table: DesignTable, design: Design) -> Outcome | None:
    if not _time_based(table):
        return None
    if any(_is_date_dimension(t) for t in _foreign_keys(table, design)):
        return PASS
    return fail(f"{table.name} has time-based measures but no foreign key to a date dimension.")


@registry.column_check(
    "measure_has_additivity",
    category=DIMENSIONAL,
    severity="warning",
    layers=MODEL,
    hint="Say whether the measure is additive, semi-additive or non-additive.",
    title="Every measure declares additivity",
)
def measure_has_additivity(
    column: DesignColumn, table: DesignTable, design: Design
) -> Outcome | None:
    if column.role != "measure":
        return None
    return PASS if column.additivity else fail(f"{column.name} does not declare additivity.")


@registry.table_check(
    "dimension_has_scd_type",
    category=DIMENSIONAL,
    severity="warning",
    layers=MODEL,
    kinds=("dimension",),
    hint="Set the dimension's SCD type (0, 1 or 2).",
    title="Every dimension declares an SCD type",
)
def dimension_has_scd_type(table: DesignTable, design: Design) -> Outcome:
    if table.scd_type is None:
        return fail(f"{table.name} does not declare an SCD type.")
    return PASS


def _snowflake_depth(table: DesignTable, design: Design, seen: frozenset = frozenset()) -> int:
    """How many dimensions deep the table's foreign keys go (cycles stop the walk)."""
    seen = seen | {table.id}
    deeper = [
        1 + _snowflake_depth(target, design, seen)
        for target in _foreign_keys(table, design)
        if target.kind == "dimension" and target.id not in seen
    ]
    return max(deeper, default=0)


@registry.table_check(
    "no_deep_snowflake",
    category=DIMENSIONAL,
    severity="info",
    layers=MODEL,
    kinds=("dimension",),
    hint="Flatten the outer dimension's attributes into this one.",
    title="Snowflaking deeper than one level",
)
def no_deep_snowflake(table: DesignTable, design: Design) -> Outcome:
    if _snowflake_depth(table, design) > 1:
        return fail(f"{table.name} reaches a dimension more than one level away.")
    return PASS


@registry.column_check(
    "fact_fk_is_lookup",
    category=DIMENSIONAL,
    severity="warning",
    layers=("core",),
    kinds=("fact",),
    hint="Map the foreign key as a lookup of the dimension on the natural key.",
    title="Every Core fact foreign key to a dimension is mapped as a lookup",
)
def fact_fk_is_lookup(column: DesignColumn, table: DesignTable, design: Design) -> Outcome | None:
    target = design.table(column.references_table_id)
    if column.role != "fk" or target is None or target.kind != "dimension":
        return None
    types = {m.mapping_type for m in column.mappings}
    if "lookup" in types:
        return PASS
    return fail(f"{column.name} is not mapped as a lookup of {target.name}.")


@registry.table_check(
    "bridge_has_keys",
    category=DIMENSIONAL,
    severity="warning",
    layers=MODEL,
    kinds=("bridge",),
    hint="Give the bridge a group key (role sk) and foreign keys to two tables.",
    title="Every bridge table has a group key and two foreign keys",
)
def bridge_has_keys(table: DesignTable, design: Design) -> Outcome:
    has_group_key = any(c.role == "sk" for c in table.columns)
    if has_group_key and sum(1 for c in table.columns if c.role == "fk") >= 2:
        return PASS
    return fail(f"{table.name} needs a group key and two foreign keys.")


# --- consistency ---------------------------------------------------------------------


@registry.table_check(
    "naming_conventions",
    category="consistency",
    severity="warning",
    layers=MODEL,
    hint="Rename the table or its columns to follow the Data Warehouse's naming rules.",
    title="Naming conventions respected",
)
def naming_conventions(table: DesignTable, design: Design) -> Outcome:
    rules = design.naming_rules
    problems = [
        f"{table.name}: {v.message}"
        for v in check_table_name(rules, layer=table.layer, kind=table.kind, name=table.name)
    ] + [
        f"{table.name}.{c.name}: {v.message}"
        for c in table.columns
        for v in check_column_name(rules, layer=table.layer, name=c.name, is_system=c.is_system)
    ]
    return fail(" ".join(problems)) if problems else PASS
