"""The registered quality checks (spec §6.11), on the default registry.

Each check is a small function over the ``Design`` snapshot; the engine (``scoring``) runs
them and scores the result. A later check is one more decorated function here (or in a module
this one imports): it needs no change to the engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from .calendar import DATE_KEY
from .lineage_sql import (
    BranchParts,
    ColumnRef,
    ParsedExpression,
    Unparsable,
    is_group_safe,
    parse_branch,
    parse_expression,
)
from .naming import check_column_name, check_table_name
from .platforms import max_identifier_length
from .scoring import (
    ALL_LAYERS,
    PASS,
    CheckRegistry,
    CheckSpec,
    Design,
    DesignColumn,
    DesignKpi,
    DesignTable,
    Finding,
    Outcome,
    fail,
    finding,
)
from .staging_naming import TYPE_FLAGS

MODEL = ("core", "mart")
STAGING = ("staging",)
DIMENSIONAL = "dimensional_modeling"
REFERENCED_BY_FACTS = ("dimension", "generated")

registry = CheckRegistry()
"""The checks the product scores with."""

_COVERED = ("direct", "hashed", "derived", "constant", "lookup", "system", "not_in_branch")
_TIME_TYPES = ("date", "time", "timestamp", "timestamptz")


def _is_generated(table: DesignTable) -> bool:
    return table.kind == "generated"


# --- reading a table's mapping SQL ----------------------------------------------------

LAYER_BELOW = {"core": "staging", "mart": "core"}
"""The Layer a Layer's data inputs come from (staging is fed by the sources)."""
_SOURCE_REMOVED = "source_removed"


@lru_cache(maxsize=8192)
def _expression(sql: str, platform: str) -> ParsedExpression | str:
    """The parsed expression, or why it does not parse (cached: several checks read it)."""
    try:
        return parse_expression(sql, platform)
    except Unparsable as exc:
        return str(exc)


@lru_cache(maxsize=2048)
def _branch(
    driving_input: str,
    joins: str,
    filters: str,
    group_by: str | None,
    having: str | None,
    platform: str,
) -> BranchParts | str:
    try:
        return parse_branch(driving_input, joins, filters, group_by, having, platform)
    except Unparsable as exc:
        return str(exc)


@dataclass(frozen=True)
class Input:
    """One column a table's mappings or branches read, resolved against the design."""

    label: str
    """``table.column`` (or a branch's ``table``) as written."""
    where: str
    """What reads it: a column, or a branch."""
    table: DesignTable | None
    column: DesignColumn | None
    whole_table: bool = False
    """A table a branch reads from (its FROM or a join), not one column of it."""

    @property
    def missing(self) -> bool:
        return self.table is None or (self.column is None and not self.whole_table)

    @property
    def source_removed(self) -> bool:
        return any(o is not None and o.status == _SOURCE_REMOVED for o in (self.table, self.column))


@dataclass(frozen=True)
class Inputs:
    unparsed: tuple[str, ...]
    """What does not parse, with the reason."""
    read: tuple[Input, ...]


def _resolve(
    ref: ColumnRef, layer: str, design: Design
) -> tuple[DesignTable | None, DesignColumn | None]:
    """The table and column ``ref`` names: in the Layer below when it is there, else in
    any Layer (so a wrong-Layer input is told apart from a missing one)."""
    candidates = design.by_name.get((ref.table or "").lower(), ())
    below = LAYER_BELOW.get(layer)
    found = next((t for t in candidates if t.layer == below), None) or next(iter(candidates), None)
    if found is None:
        return None, None
    column = next((c for c in found.columns if c.name.lower() == ref.column.lower()), None)
    return found, column


def table_inputs(table: DesignTable, design: Design) -> Inputs:
    """Every column the table's column mappings (a lookup's natural-key inputs included, its
    dimension not: that is a declared reference) and branches read. Unqualified names in a
    branch's conditions are left out: they cannot be resolved to one table."""
    unparsed: list[str] = []
    read: list[Input] = []

    def add(refs: tuple[ColumnRef, ...] | list[ColumnRef], where: str) -> None:
        for ref in refs:
            if ref.table is None:
                continue
            t, c = _resolve(ref, table.layer, design)
            read.append(Input(f"{ref.table}.{ref.column}", where, t, c))

    branch_names = {b.id: b.name for b in table.branches}
    for column in table.columns:
        for m in column.mappings:
            where = column.name + (
                f" (branch {branch_names[m.branch_id]})" if m.branch_id in branch_names else ""
            )
            for text in m.lookup_inputs:
                parsed = _expression(text, design.platform)
                if isinstance(parsed, str):
                    unparsed.append(f"{where}: {parsed}")
                else:
                    add(parsed.value, where)
            if not m.sql_expression or m.mapping_type in ("lookup", "system", "unmapped"):
                continue
            parsed = _expression(m.sql_expression, design.platform)
            if isinstance(parsed, str):
                unparsed.append(f"{where}: {parsed}")
            else:
                add([*parsed.value, *parsed.uses], where)
    for branch in table.branches:
        parts = _branch(
            branch.driving_input,
            branch.joins,
            branch.filters,
            branch.group_by,
            branch.having,
            design.platform,
        )
        where = f"branch {branch.name}"
        if isinstance(parts, str):
            unparsed.append(f"{where}: {parts}")
            continue
        for name in parts.tables:
            # A name that is no DW table may be a CTE or a derived table: only the columns
            # read through it are checked, not the name itself.
            t, _ = _resolve(ColumnRef(name, ""), table.layer, design)
            if t is not None:
                read.append(Input(name, where, t, None, whole_table=True))
        add(parts.refs, where)
    return Inputs(tuple(unparsed), tuple(read))


def _has_sql(table: DesignTable) -> bool:
    return bool(table.branches) or any(
        m.sql_expression or m.lookup_inputs for c in table.columns for m in c.mappings
    )


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


def is_date_dimension(table: DesignTable) -> bool:
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
    if any(is_date_dimension(t) for t in _foreign_keys(table, design)):
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


STAGING_PREFIX = "stg_"


def _staging_naming(table: DesignTable, design: Design) -> list[str]:
    """Staging's own convention (spec §6.7): ``stg_`` tables, lower case, and every name
    within the target platform's identifier limit."""
    limit = max_identifier_length(design.platform)  # type: ignore[arg-type]
    problems: list[str] = []
    if not table.name.startswith(STAGING_PREFIX):
        problems.append(f"{table.name}: A Staging Table name should start with {STAGING_PREFIX!r}.")
    for label, name in [(table.name, table.name)] + [
        (f"{table.name}.{c.name}", c.name) for c in table.columns
    ]:
        if name != name.lower():
            problems.append(f"{label}: The name should be in lower case.")
        if len(name.encode()) > limit:
            problems.append(f"{label}: The name is over the platform's {limit}-byte limit.")
    return problems


@registry.table_check(
    "naming_conventions",
    category="consistency",
    severity="warning",
    layers=ALL_LAYERS,
    hint="Rename the table or its columns to follow the Data Warehouse's naming rules.",
    title="Naming conventions respected",
)
def naming_conventions(table: DesignTable, design: Design) -> Outcome:
    if table.layer == "staging":
        found = _staging_naming(table, design)
        return fail(" ".join(found)) if found else PASS
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


def _type_label(column: DesignColumn) -> str:
    t = column.data_type
    if t.get("length"):
        return f"{t.get('type')}({t['length']})"
    if t.get("precision") is not None:
        return f"{t.get('type')}({t['precision']},{t.get('scale') or 0})"
    return str(t.get("type", "?"))


def _type_key(column: DesignColumn) -> tuple:
    """The type as ``_type_label`` shows it: an unset scale is 0, empty fields don't count."""
    t = dict(column.data_type)
    if t.get("precision") is not None:
        t["scale"] = t.get("scale") or 0
    return tuple(sorted((k, str(v)) for k, v in t.items() if v is not None))


@registry.check(
    "same_name_same_type",
    category="consistency",
    severity="warning",
    layers=MODEL,
    hint="Give the same-named columns one data type, or rename the one that means something else.",
    title="Same-named columns across tables share the same data type",
)
def same_name_same_type(design: Design, spec: CheckSpec) -> Iterator[Finding]:
    # One pass to group the columns by name and type, so a wide model stays linear.
    by_name: dict[str, dict[tuple, list[tuple[DesignTable, DesignColumn]]]] = {}
    for t in design.tables:
        if t.layer in MODEL:
            for c in t.columns:
                if not c.is_system:
                    by_name.setdefault(c.name.lower(), {}).setdefault(_type_key(c), []).append(
                        (t, c)
                    )
    for t in design.tables:
        if t.layer not in MODEL:
            continue
        for c in t.columns:
            if c.is_system:
                continue
            types = by_name[c.name.lower()]
            if len(types) == 1:
                yield finding(spec, PASS, t, c)
                continue
            mine = _type_key(c)
            where = ", ".join(
                f"{other.name} ({_type_label(oc)})"
                for key, group in types.items()
                if key != mine
                for other, oc in group
            )
            message = f"{c.name} is {_type_label(c)} here but differs in {where}."
            yield finding(spec, fail(message), t, c)


@registry.table_check(
    "inputs_from_layer_below",
    category="consistency",
    severity="error",
    layers=ALL_LAYERS,
    hint="Map the table from the Layer directly below (Core from staging, Mart from Core).",
    title="Each Layer's data inputs come only from the Layer below",
)
def inputs_from_layer_below(table: DesignTable, design: Design) -> Outcome | None:
    below = LAYER_BELOW.get(table.layer)
    if below is None or _is_generated(table):
        return None  # staging is fed by the sources only
    found = [i for i in table_inputs(table, design).read if i.table is not None]
    if not found:
        return None
    wrong = sorted(
        {f"{i.label} ({i.table.layer})" for i in found if i.table and i.table.layer != below}
    )
    if wrong:
        return fail(f"{table.name} reads outside the {below} Layer: {', '.join(wrong)}.")
    return PASS


@registry.table_check(
    "conformed_dimension_shared",
    category="consistency",
    severity="info",
    layers=MODEL,
    kinds=("dimension",),
    hint="Link the dimension from a second fact, or stop marking it conformed.",
    title="Conformed dimensions used by at least two facts",
)
def conformed_dimension_shared(table: DesignTable, design: Design) -> Outcome | None:
    if not table.is_conformed:
        return None
    facts = {
        t.id
        for t in design.tables
        if t.kind == "fact"
        and any(c.role == "fk" and c.references_table_id == table.id for c in t.columns)
    }
    if len(facts) >= 2:
        return PASS
    return fail(f"{table.name} is conformed but used by {len(facts)} fact(s).")


# --- traceability --------------------------------------------------------------------


@registry.table_check(
    "mapping_parses",
    category="traceability",
    severity="error",
    layers=ALL_LAYERS,
    hint="Fix the SQL so it is one valid expression (or one query, for a branch).",
    title="Every mapping expression, join and filter parses",
)
def mapping_parses(table: DesignTable, design: Design) -> Outcome | None:
    if _is_generated(table) or not _has_sql(table):
        return None
    unparsed = table_inputs(table, design).unparsed
    if unparsed:
        return fail(f"Does not parse: {'; '.join(unparsed)}.")
    return PASS


@registry.table_check(
    "inputs_exist",
    category="traceability",
    severity="error",
    layers=ALL_LAYERS,
    hint="Point the mapping at a column that exists, or add the column.",
    title="All mapped input columns exist",
)
def inputs_exist(table: DesignTable, design: Design) -> Outcome | None:
    if _is_generated(table) or table.layer == "staging":
        return None  # staging reads the Source Schema; a source gone is source_removed
    read = table_inputs(table, design).read
    if not read:
        return None
    missing = sorted({f"{i.label} (in {i.where})" for i in read if i.missing})
    if missing:
        return fail(f"Not found: {', '.join(missing)}.")
    return PASS


@registry.table_check(
    "no_source_removed_inputs",
    category="traceability",
    severity="error",
    layers=ALL_LAYERS,
    hint="Map the column from something still in the source, or retire it.",
    title="No mapping references a column flagged source_removed",
)
def no_source_removed_inputs(table: DesignTable, design: Design) -> Outcome | None:
    if table.layer == "staging":
        # A staging object's mapping is its source: gone from the source, it is a warning.
        removed = [table.name] if table.status == _SOURCE_REMOVED else []
        removed += [f"{table.name}.{c.name}" for c in table.columns if c.status == _SOURCE_REMOVED]
        if removed:
            return fail(f"Removed from the source: {', '.join(removed)}.", severity="warning")
        return PASS
    if _is_generated(table):
        return None
    read = table_inputs(table, design).read
    if not read:
        return None
    removed = sorted({f"{i.label} (in {i.where})" for i in read if i.source_removed})
    if removed:
        return fail(f"Reads what was removed from the source: {', '.join(removed)}.")
    return PASS


def _open_flags(flags, edited_fields) -> list[str]:
    """The flags not yet resolved: a flag is resolved once the user overrides the field it
    is about."""
    return [
        str(f.get("message") or f.get("code"))
        for f in flags
        if ("data_type" if f.get("code") in TYPE_FLAGS else "name") not in edited_fields
    ]


@registry.table_check(
    "review_flags_resolved",
    category="traceability",
    severity="warning",
    layers=STAGING,
    hint="Check the flagged name or type and set it yourself; an override resolves the flag.",
    title="Generated names and type translations flagged for review are resolved",
)
def review_flags_resolved(table: DesignTable, design: Design) -> Outcome:
    open_flags = [
        f"{table.name}: {m}" for m in _open_flags(table.review_flags, table.edited_fields)
    ]
    open_flags += [
        f"{table.name}.{c.name}: {m}"
        for c in table.columns
        for m in _open_flags(c.review_flags, c.edited_fields)
    ]
    return fail(" ".join(open_flags)) if open_flags else PASS


# --- documentation -------------------------------------------------------------------


@registry.table_check(
    "table_has_description",
    category="documentation",
    severity="info",
    layers=MODEL,
    hint="Describe what the table holds and what it is for.",
    title="Every DW table has a description",
)
def table_has_description(table: DesignTable, design: Design) -> Outcome:
    if table.description.strip():
        return PASS
    return fail(f"{table.name} has no description.")


@registry.column_check(
    "measure_has_description",
    category="documentation",
    severity="info",
    layers=MODEL,
    hint="Describe what the measure counts or sums, and in what unit.",
    title="Every measure has a description",
)
def measure_has_description(
    column: DesignColumn, table: DesignTable, design: Design
) -> Outcome | None:
    if column.role != "measure":
        return None
    return PASS if column.description.strip() else fail(f"{column.name} has no description.")


@registry.kpi_check(
    "kpi_has_description",
    category="documentation",
    severity="info",
    hint="Write the KPI's business definition.",
    title="Every KPI has a description",
)
def kpi_has_description(kpi: DesignKpi, design: Design) -> Outcome:
    return PASS if kpi.description.strip() else fail(f"{kpi.name} has no description.")


# --- DW-wide KPI completeness ---------------------------------------------------------


@registry.kpi_check(
    "kpi_links_measure",
    category="completeness",
    severity="warning",
    hint="Link the KPI to the measure columns its formula uses.",
    title="Every KPI links to at least one measure",
)
def kpi_links_measure(kpi: DesignKpi, design: Design) -> Outcome:
    measures = {
        c.id for t in design.tables if t.layer in MODEL for c in t.columns if c.role == "measure"
    }
    if any(i in measures for i in kpi.linked_column_ids):
        return PASS
    return fail(f"{kpi.name} is not linked to any measure.")


# --- performance readiness -----------------------------------------------------------

_INTEGER_TYPES = ("smallint", "integer", "bigint")
_TEXT_TYPES = ("char", "string", "text")
MAX_FACT_WIDTH = 60


@registry.column_check(
    "surrogate_key_integer",
    category="performance_readiness",
    severity="warning",
    layers=MODEL,
    hint="Make the surrogate key an integer type (smallint, integer or bigint).",
    title="Surrogate keys use integer types",
)
def surrogate_key_integer(
    column: DesignColumn, table: DesignTable, design: Design
) -> Outcome | None:
    if column.role != "sk":
        return None
    # The date dimension's YYYYMMDD key is an integer too, so it needs no exception here.
    if column.data_type.get("type") in _INTEGER_TYPES:
        return PASS
    return fail(f"{column.name} is {_type_label(column)}, not an integer.")


@registry.column_check(
    "fact_no_free_text",
    category="performance_readiness",
    severity="warning",
    layers=MODEL,
    kinds=("fact",),
    hint="Move the text to a dimension, or mark it a degenerate dimension if it is an id.",
    title="No free-text attributes or text measures on fact tables",
)
def fact_no_free_text(column: DesignColumn, table: DesignTable, design: Design) -> Outcome | None:
    if column.role not in ("attribute", "measure"):
        return None  # degenerate dimensions (and keys) are exempt
    if column.data_type.get("type") in _TEXT_TYPES:
        return fail(f"{column.name} is a text {column.role} on a fact table.")
    return PASS


@registry.table_check(
    "fact_width",
    category="performance_readiness",
    severity="info",
    layers=MODEL,
    kinds=("fact",),
    hint="Split the fact, or move descriptive columns to dimensions.",
    title=f"Fact tables no wider than {MAX_FACT_WIDTH} columns",
)
def fact_width(table: DesignTable, design: Design) -> Outcome:
    if len(table.columns) > MAX_FACT_WIDTH:
        return fail(f"{table.name} has {len(table.columns)} columns.")
    return PASS


# --- privacy -------------------------------------------------------------------------


@registry.table_check(
    "pii_in_mart",
    category="privacy",
    severity="info",
    layers=("mart",),
    hint="Leave the PII out of the Mart, or hash it in staging if the Mart needs it.",
    title="PII-derived columns reach the mart Layer",
)
def pii_in_mart(table: DesignTable, design: Design) -> Outcome:
    derived = [c.name for c in table.columns if c.pii_derived]
    if derived:
        return fail(f"PII-derived columns in the Mart: {', '.join(derived)}.")
    return PASS
