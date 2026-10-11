"""Rule-based KPI suggestions and feasibility (spec §6.13, story 77).

Pure: the DW Schema and its mapping state in, suggestions out; nothing here reads the
database. For each fact of the Core and Mart Layers there is a ``SUM`` and ``AVG`` of every
additive measure, a ``COUNT(DISTINCT)`` of every dimension key, a ratio of every pair of
additive measures, and month-over-month and year-over-year growth of every additive measure
when the fact has a key to a date dimension. The formula SQL is written against the DW Schema
in the target platform's dialect.

A suggestion's ``key`` names what it measures by the **Core** column it traces to through
``value`` lineage, so a Core fact and a Mart aggregate of the same measure are one suggestion
(shown at the Mart column: a KPI links to the highest Layer that holds the measure), and
dismissing it survives the measure moving up a Layer.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dawam.modules.warehouse import (
    SQL_DIALECTS,
    DwSchema,
    MappingState,
    SchemaColumn,
    SchemaTable,
    TargetPlatform,
    ident,
)

from .links import _table_of

COMPUTABLE = "computable"
NEEDS_MAPPING = "needs_mapping"
NEEDS_DATA = "needs_data"
FEASIBILITIES = (COMPUTABLE, NEEDS_MAPPING, NEEDS_DATA)

KINDS = ("sum", "avg", "count_distinct", "ratio", "mom_growth", "yoy_growth")

MAX_RATIO_MEASURES = 5
"""Ratios are offered for ordered pairs of a fact's first measures only."""

_TRACE_DEPTH = 8


@dataclass(frozen=True)
class RuleSuggestion:
    key: str
    """Stable across renames and across a measure moving from Core to Mart."""
    kind: str
    name: str
    definition: str
    formula_text: str
    formula_sql: str
    aggregation: str
    layer: str
    table_name: str
    column_ids: tuple[uuid.UUID, ...]
    """The DW columns the KPI will link to."""
    feasibility: str


def feasibility_of(
    inputs: Iterable[uuid.UUID], existing: Collection[uuid.UUID], mapped: Collection[uuid.UUID]
) -> str:
    """*needs data* if an input is missing from the DW Schema, else *needs mapping* if one
    is not filled by a mapping, else *computable*."""
    wanted = list(inputs)
    if any(i not in existing for i in wanted):
        return NEEDS_DATA
    if any(i not in mapped for i in wanted):
        return NEEDS_MAPPING
    return COMPUTABLE


def formula_columns(schema: DwSchema, sql: str) -> set[uuid.UUID] | None:
    """The Core and Mart columns a formula reads, or ``None`` if it names a table that is not
    one, or does not parse (its inputs are missing from the DW Schema)."""
    try:
        trees = sqlglot.parse(sql, read=SQL_DIALECTS.get(schema.platform))
    except SqlglotError:
        return None
    tree = next((t for t in trees if t is not None), None)
    if tree is None:
        return None
    opaque = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    opaque |= {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    outputs = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    sources: dict[str, SchemaTable] = {}
    for reference in tree.find_all(exp.Table):
        if not isinstance(reference.this, exp.Identifier):
            continue
        if not reference.db and reference.name.lower() in opaque:
            continue
        table = _table_of(schema, reference)
        if table is None:
            return None
        sources[(reference.alias or reference.name).lower()] = table
    found: set[uuid.UUID] = set()
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        name = column.name.lower()
        candidates = (
            [sources[column.table.lower()]]
            if column.table and column.table.lower() in sources
            else list(sources.values())
        )
        hits = [c for t in candidates for c in t.columns if c.name.lower() == name]
        if not hits and name not in outputs and not opaque and not column.table:
            return None
        found.update(c.id for c in hits)
    return found


class _Model:
    """The schema with the lineage lookups the rules need."""

    def __init__(self, schema: DwSchema, state: MappingState) -> None:
        self.schema = schema
        self.state = state
        self.columns: dict[uuid.UUID, SchemaColumn] = {
            c.id: c for t in schema.tables for c in t.columns
        }
        self.tables = {t.id: t for t in schema.tables}
        self.sources: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        self.readers: set[uuid.UUID] = set()
        for source, target in state.value_edges:
            if source in self.columns and target in self.columns:
                self.sources[target].append(source)
                if self.columns[target].layer == "mart" and self.columns[source].layer == "core":
                    self.readers.add(source)

    def root(self, column: SchemaColumn) -> uuid.UUID:
        """The Core column ``column`` is a copy of through lineage: its one input, again and
        again; ``column`` itself once it is read from several inputs or from none."""
        current = column
        for _ in range(_TRACE_DEPTH):
            inputs = {i for i in self.sources.get(current.id, ()) if i != current.id}
            if len(inputs) != 1:
                break
            current = self.columns[next(iter(inputs))]
        return current.id

    def date_dimension(
        self, fact: SchemaTable
    ) -> tuple[SchemaColumn, SchemaTable, SchemaColumn] | None:
        """The first key of the fact to a date dimension, that dimension and its key."""
        for column in fact.columns:
            if column.role != "fk" or column.references_table_id is None:
                continue
            dim = self.tables.get(column.references_table_id)
            if dim is None or dim.kind not in ("generated", "dimension"):
                continue
            names = {c.name.lower() for c in dim.columns}
            key = next((c for c in dim.columns if c.role == "sk"), None)
            if key is not None and {"year", "month", "year_month"} <= names:
                return column, dim, key
        return None


def _title(name: str) -> str:
    return " ".join(name.replace("_", " ").split())


def _key_base(name: str) -> str:
    lowered = name.lower()
    for suffix in ("_key", "_sk", "_id", "_fk"):
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _id(schema: DwSchema, name: str) -> str:
    return ident(cast(TargetPlatform, schema.platform), name)


def _qualified(schema: DwSchema, table: SchemaTable) -> str:
    physical = schema.layer_schemas.get(table.layer)
    return (
        f"{_id(schema, physical)}.{_id(schema, table.name)}"
        if physical
        else _id(schema, table.name)
    )


def _col(schema: DwSchema, name: str, alias: str | None = None) -> str:
    text = _id(schema, name)
    return f"{alias}.{text}" if alias else text


def suggest(schema: DwSchema, state: MappingState) -> list[RuleSuggestion]:
    """Every rule-based suggestion for the schema, deduplicated, by name."""
    model = _Model(schema, state)
    found: dict[str, RuleSuggestion] = {}
    # The highest Layer holds the measure; among equals the first table by name.
    facts = sorted(
        (t for t in schema.tables if t.kind == "fact"),
        key=lambda t: (t.layer != "mart", t.name.lower()),
    )
    for fact in facts:
        for suggestion in _of_fact(model, fact):
            found.setdefault(suggestion.key, suggestion)
    return sorted(found.values(), key=lambda s: (s.name.lower(), s.key))


def _of_fact(model: _Model, fact: SchemaTable) -> list[RuleSuggestion]:
    schema = model.schema
    # A Core column a Mart column is read from is represented by that Mart column.
    live = [c for c in fact.columns if c.id not in model.readers]
    measures = [c for c in live if c.role == "measure" and c.additivity == "additive"]
    out: list[RuleSuggestion] = []
    table_sql = _qualified(schema, fact)

    def make(
        kind: str,
        key_parts: Sequence[uuid.UUID],
        name: str,
        definition: str,
        formula_text: str,
        sql: str,
        aggregation: str,
        links: Sequence[SchemaColumn],
        extra_inputs: Sequence[uuid.UUID] = (),
    ) -> RuleSuggestion:
        inputs = [c.id for c in links] + list(extra_inputs)
        return RuleSuggestion(
            key=f"{kind}:" + ":".join(str(p) for p in key_parts),
            kind=kind,
            name=name,
            definition=definition,
            formula_text=formula_text,
            formula_sql=sql,
            aggregation=aggregation,
            layer=fact.layer,
            table_name=fact.name,
            column_ids=tuple(c.id for c in links),
            feasibility=feasibility_of(inputs, model.columns, model.state.mapped),
        )

    for measure in measures:
        root = model.root(measure)
        col = _col(schema, measure.name)
        label = f"{measure.name} ({fact.name})"
        for kind, func, word in (("sum", "SUM", "Sum"), ("avg", "AVG", "Average")):
            alias = _id(schema, f"{func.lower()}_{measure.name}")
            out.append(
                make(
                    kind,
                    [root],
                    f"{word} of {label}",
                    f"{word} of {_title(measure.name)} over all rows of {fact.name}.",
                    f"{func}({measure.name})",
                    f"SELECT {func}({col}) AS {alias} FROM {table_sql}",
                    kind,
                    [measure],
                )
            )
    date = model.date_dimension(fact)
    for column in live:
        if column.role != "fk" or column.references_table_id is None:
            continue
        dim = model.tables.get(column.references_table_id)
        if dim is None or (date is not None and dim.id == date[1].id):
            continue
        alias = _id(schema, f"distinct_{column.name}")
        out.append(
            make(
                "count_distinct",
                [model.root(column)],
                f"Distinct {_title(_key_base(column.name))} count ({fact.name})",
                f"Number of distinct {_title(_key_base(column.name))} values in {fact.name}.",
                f"COUNT(DISTINCT {column.name})",
                f"SELECT COUNT(DISTINCT {_col(schema, column.name)}) AS {alias} FROM {table_sql}",
                "count_distinct",
                [column],
            )
        )
    pool = measures[:MAX_RATIO_MEASURES]
    for top in pool:
        for bottom in pool:
            if top.id == bottom.id:
                continue
            alias = _id(schema, f"{top.name}_per_{bottom.name}")
            out.append(
                make(
                    "ratio",
                    [model.root(top), model.root(bottom)],
                    f"{_title(top.name).capitalize()} per {_title(bottom.name)} ({fact.name})",
                    f"Total {_title(top.name)} divided by total {_title(bottom.name)}.",
                    f"SUM({top.name}) / SUM({bottom.name})",
                    f"SELECT SUM({_col(schema, top.name)}) / "
                    f"NULLIF(SUM({_col(schema, bottom.name)}), 0) AS {alias} FROM {table_sql}",
                    "ratio",
                    [top, bottom],
                )
            )
    if date is not None:
        out.extend(_growth(model, fact, measures, date, make))
    return out


def _growth(
    model: _Model,
    fact: SchemaTable,
    measures: Sequence[SchemaColumn],
    date: tuple[SchemaColumn, SchemaTable, SchemaColumn],
    make: Callable[..., RuleSuggestion],
) -> list[RuleSuggestion]:
    schema = model.schema
    fk, dim, key = date
    names = {c.name.lower(): c for c in dim.columns}
    join = (
        f"FROM {_qualified(schema, fact)} f JOIN {_qualified(schema, dim)} d "
        f"ON {_col(schema, fk.name, 'f')} = {_col(schema, key.name, 'd')}"
    )
    out: list[RuleSuggestion] = []
    for measure in measures:
        total = _col(schema, measure.name, "f")
        label = f"{measure.name} ({fact.name})"
        ym, year, month = names["year_month"], names["year"], names["month"]
        mom = (
            f"WITH period_totals AS (SELECT {_col(schema, ym.name, 'd')} AS period_month, "
            f"SUM({total}) AS period_total {join} GROUP BY {_col(schema, ym.name, 'd')}) "
            "SELECT period_month, period_total, "
            "period_total / NULLIF(LAG(period_total) OVER (ORDER BY period_month), 0) - 1 "
            "AS mom_growth FROM period_totals ORDER BY period_month"
        )
        yoy = (
            f"WITH period_totals AS (SELECT {_col(schema, year.name, 'd')} AS period_year, "
            f"{_col(schema, month.name, 'd')} AS period_month, SUM({total}) AS period_total {join} "
            f"GROUP BY {_col(schema, year.name, 'd')}, {_col(schema, month.name, 'd')}) "
            "SELECT period_year, period_month, period_total, "
            "period_total / NULLIF(LAG(period_total) OVER "
            "(PARTITION BY period_month ORDER BY period_year), 0) - 1 "
            "AS yoy_growth FROM period_totals ORDER BY period_year, period_month"
        )
        root = model.root(measure)
        for kind, sql, used, word, versus in (
            ("mom_growth", mom, [ym], "Month-over-month", "the previous month"),
            ("yoy_growth", yoy, [year, month], "Year-over-year", "the same month a year earlier"),
        ):
            out.append(
                make(
                    kind,
                    [root],
                    f"{word} growth of {label}",
                    f"Change of the monthly sum of {_title(measure.name)} against {versus}.",
                    f"(SUM({measure.name}) this month / SUM({measure.name}) {versus}) - 1",
                    sql,
                    kind,
                    [measure],
                    extra_inputs=[fk.id, key.id, *(c.id for c in used)],
                )
            )
    return out
