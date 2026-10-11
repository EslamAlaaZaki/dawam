"""Rule-based KPI suggestions on fixture DW Schemas (spec §6.13, story 77).

Pure tests: a hand-built DW Schema and mapping state in, the expected suggestions out.
"""

from __future__ import annotations

import uuid

import pytest
import sqlglot

from dawam.modules.kpis.links import validate_formula
from dawam.modules.kpis.suggestions import (
    COMPUTABLE,
    NEEDS_DATA,
    NEEDS_MAPPING,
    RuleSuggestion,
    feasibility_of,
    formula_columns,
    suggest,
)
from dawam.modules.warehouse import SQL_DIALECTS, DwSchema, MappingState, SchemaColumn, SchemaTable


class Builder:
    """Builds a Core/Mart schema column by column."""

    def __init__(self, platform: str = "postgresql") -> None:
        self.platform = platform
        self.tables: list[SchemaTable] = []
        self.edges: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.mapped: set[uuid.UUID] = set()

    def table(
        self, layer: str, name: str, kind: str, columns: list[dict], *, mapped: bool = True
    ) -> SchemaTable:
        table_id = uuid.uuid4()
        built = tuple(
            SchemaColumn(
                id=uuid.uuid4(),
                table_id=table_id,
                table_name=name,
                layer=layer,
                name=c["name"],
                role=c.get("role", "attribute"),
                additivity=c.get("additivity"),
                references_table_id=c.get("references"),
            )
            for c in columns
        )
        table = SchemaTable(table_id, layer, name, kind, built)
        self.tables.append(table)
        if mapped:
            self.mapped.update(c.id for c in built)
        return table

    def read(self, source: SchemaColumn, target: SchemaColumn) -> None:
        self.edges.append((source.id, target.id))

    def build(self) -> tuple[DwSchema, MappingState]:
        schema = DwSchema(
            platform=self.platform,
            layer_schemas={"core": "core", "mart": "mart"},
            tables=tuple(self.tables),
        )
        return schema, MappingState(tuple(self.edges), frozenset(self.mapped))


def col(table: SchemaTable, name: str) -> SchemaColumn:
    return next(c for c in table.columns if c.name == name)


def measure(name: str, additivity: str = "additive") -> dict:
    return {"name": name, "role": "measure", "additivity": additivity}


def date_dim(b: Builder) -> SchemaTable:
    return b.table(
        "core",
        "dim_date",
        "generated",
        [
            {"name": "date_key", "role": "sk"},
            {"name": "year"},
            {"name": "month"},
            {"name": "year_month"},
        ],
    )


def retail(b: Builder, *, with_date: bool = True) -> SchemaTable:
    customer = b.table(
        "core", "dim_customer", "dimension", [{"name": "customer_key", "role": "sk"}]
    )
    columns = [
        {"name": "customer_key", "role": "fk", "references": customer.id},
        measure("revenue"),
        measure("quantity"),
        measure("unit_price", "non_additive"),
        {"name": "order_no", "role": "degenerate_dimension"},
    ]
    if with_date:
        dim = date_dim(b)
        columns.insert(0, {"name": "order_date_key", "role": "fk", "references": dim.id})
    return b.table("core", "fact_sales", "fact", columns)


def by_kind(suggestions: list[RuleSuggestion], kind: str) -> list[str]:
    return sorted(s.name for s in suggestions if s.kind == kind)


def test_a_fact_gets_the_rule_based_suggestions_for_its_additive_measures_and_keys():
    b = Builder()
    retail(b)

    suggestions = suggest(*b.build())

    assert by_kind(suggestions, "sum") == [
        "Sum of quantity (fact_sales)",
        "Sum of revenue (fact_sales)",
    ]
    assert by_kind(suggestions, "avg") == [
        "Average of quantity (fact_sales)",
        "Average of revenue (fact_sales)",
    ]
    # the non-additive unit price is never summed; the date key is no "key dimension"
    assert by_kind(suggestions, "count_distinct") == ["Distinct customer count (fact_sales)"]
    assert by_kind(suggestions, "ratio") == [
        "Quantity per revenue (fact_sales)",
        "Revenue per quantity (fact_sales)",
    ]
    assert by_kind(suggestions, "mom_growth") == [
        "Month-over-month growth of quantity (fact_sales)",
        "Month-over-month growth of revenue (fact_sales)",
    ]
    assert by_kind(suggestions, "yoy_growth") == [
        "Year-over-year growth of quantity (fact_sales)",
        "Year-over-year growth of revenue (fact_sales)",
    ]
    assert len(suggestions) == 11
    assert all(s.feasibility == COMPUTABLE for s in suggestions)


def test_the_formula_sql_is_written_against_the_dw_schema():
    b = Builder()
    retail(b)
    schema, state = b.build()

    suggestions = {s.kind + ":" + s.name: s for s in suggest(schema, state)}

    assert (
        suggestions["sum:Sum of revenue (fact_sales)"].formula_sql
        == "SELECT SUM(revenue) AS sum_revenue FROM core.fact_sales"
    )
    assert (
        suggestions["count_distinct:Distinct customer count (fact_sales)"].formula_sql
        == "SELECT COUNT(DISTINCT customer_key) AS distinct_customer_key FROM core.fact_sales"
    )
    assert (
        suggestions["ratio:Revenue per quantity (fact_sales)"].formula_sql
        == "SELECT SUM(revenue) / NULLIF(SUM(quantity), 0) AS revenue_per_quantity "
        "FROM core.fact_sales"
    )
    for suggestion in suggestions.values():
        validate_formula(schema, suggestion.formula_sql)  # fits the DW Schema, one SELECT
        sqlglot.parse_one(suggestion.formula_sql, read=SQL_DIALECTS["postgresql"])


def test_growth_joins_the_date_dimension_by_month_and_by_year():
    b = Builder()
    retail(b)

    growth = {s.kind: s for s in suggest(*b.build()) if s.name.endswith("of revenue (fact_sales)")}

    assert "LAG(period_total) OVER (ORDER BY period_month)" in growth["mom_growth"].formula_sql
    assert "d.year_month" in growth["mom_growth"].formula_sql
    assert "f.order_date_key = d.date_key" in growth["mom_growth"].formula_sql
    assert "PARTITION BY period_month ORDER BY period_year" in growth["yoy_growth"].formula_sql


def test_no_growth_without_a_date_dimension():
    b = Builder()
    retail(b, with_date=False)

    kinds = {s.kind for s in suggest(*b.build())}

    assert kinds == {"sum", "avg", "count_distinct", "ratio"}


def test_a_dimension_or_a_fact_without_measures_gets_only_its_keys():
    b = Builder()
    customer = b.table("core", "dim_customer", "dimension", [measure("score")])
    b.table(
        "core",
        "fact_visit",
        "fact",
        [{"name": "customer_key", "role": "fk", "references": customer.id}],
    )

    suggestions = suggest(*b.build())

    assert [(s.kind, s.table_name) for s in suggestions] == [("count_distinct", "fact_visit")]


def test_a_core_fact_and_its_mart_aggregate_of_the_same_measure_are_one_suggestion():
    b = Builder()
    core = b.table("core", "fact_loan", "fact", [measure("amount")])
    mart = b.table("mart", "mart_loan_monthly", "fact", [measure("amount_total")])
    b.read(col(core, "amount"), col(mart, "amount_total"))

    suggestions = suggest(*b.build())

    sums = [s for s in suggestions if s.kind == "sum"]
    assert [(s.layer, s.table_name, s.column_ids) for s in sums] == [
        ("mart", "mart_loan_monthly", (col(mart, "amount_total").id,))
    ]
    assert len([s for s in suggestions if s.kind == "avg"]) == 1


def test_two_mart_aggregates_of_one_core_measure_are_one_suggestion():
    b = Builder()
    core = b.table("core", "fact_loan", "fact", [measure("amount")])
    monthly = b.table("mart", "mart_loan_monthly", "fact", [measure("amount_m")])
    branch = b.table("mart", "mart_loan_branch", "fact", [measure("amount_b")])
    b.read(col(core, "amount"), col(monthly, "amount_m"))
    b.read(col(core, "amount"), col(branch, "amount_b"))

    sums = [s for s in suggest(*b.build()) if s.kind == "sum"]

    assert [s.table_name for s in sums] == ["mart_loan_branch"]


def test_the_key_survives_a_measure_moving_up_to_the_mart():
    before = Builder()
    core = before.table("core", "fact_loan", "fact", [measure("amount")])
    [sum_before] = [s for s in suggest(*before.build()) if s.kind == "sum"]

    after = Builder()
    after.tables = list(before.tables)
    after.mapped = set(before.mapped)
    mart = after.table("mart", "mart_loan", "fact", [measure("amount_m")])
    after.read(col(core, "amount"), col(mart, "amount_m"))
    [sum_after] = [s for s in suggest(*after.build()) if s.kind == "sum"]

    assert sum_after.key == sum_before.key
    assert sum_after.layer == "mart"


def test_two_measures_derived_from_different_core_columns_stay_separate():
    b = Builder()
    core = b.table("core", "fact_loan", "fact", [measure("principal"), measure("interest")])
    mart = b.table("mart", "mart_loan", "fact", [measure("total")])
    b.read(col(core, "principal"), col(mart, "total"))
    b.read(col(core, "interest"), col(mart, "total"))

    sums = [s for s in suggest(*b.build()) if s.kind == "sum"]

    assert [s.table_name for s in sums] == ["mart_loan"]


def test_feasibility_is_computable_needs_mapping_or_needs_data():
    a, b, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    existing = {a, b}

    assert feasibility_of([a, b], existing, {a, b}) == COMPUTABLE
    assert feasibility_of([a, b], existing, {a}) == NEEDS_MAPPING
    assert feasibility_of([a, gone], existing, {a}) == NEEDS_DATA
    assert feasibility_of([], existing, set()) == COMPUTABLE


def test_a_suggestion_over_an_unmapped_measure_needs_mapping():
    b = Builder()
    fact = b.table("core", "fact_loan", "fact", [measure("amount"), measure("fee")], mapped=False)
    b.mapped.add(col(fact, "amount").id)

    by_name = {s.name: s for s in suggest(*b.build())}

    assert by_name["Sum of amount (fact_loan)"].feasibility == COMPUTABLE
    assert by_name["Sum of fee (fact_loan)"].feasibility == NEEDS_MAPPING
    assert by_name["Fee per amount (fact_loan)"].feasibility == NEEDS_MAPPING


def test_growth_needs_the_date_dimension_to_be_mapped_too():
    b = Builder()
    retail(b)
    schema, state = b.build()
    dim_keys = {c.id for t in schema.tables if t.name == "dim_date" for c in t.columns}
    state = MappingState(state.value_edges, frozenset(state.mapped - dim_keys))

    growth = [s for s in suggest(schema, state) if s.kind.endswith("growth")]

    assert growth
    assert all(s.feasibility == NEEDS_MAPPING for s in growth)


def test_formula_columns_finds_what_a_formula_reads():
    b = Builder()
    fact = retail(b)
    schema, _ = b.build()

    read = formula_columns(
        schema, "SELECT SUM(revenue) / NULLIF(SUM(quantity), 0) FROM core.fact_sales"
    )

    assert read == {col(fact, "revenue").id, col(fact, "quantity").id}
    assert formula_columns(schema, "SELECT SUM(x) FROM core.nowhere") is None
    assert formula_columns(schema, "SELECT SUM(nope) FROM core.fact_sales") is None
    assert formula_columns(schema, "SELECT FROM WHERE") is None


@pytest.mark.parametrize("platform", ["sqlserver", "bigquery", "snowflake", "oracle"])
def test_the_formulas_parse_in_every_target_platform(platform):
    b = Builder(platform)
    retail(b)
    schema, state = b.build()

    for suggestion in suggest(schema, state):
        validate_formula(schema, suggestion.formula_sql)


def test_suggestions_are_ordered_by_name_with_stable_unique_keys():
    b = Builder()
    retail(b)

    first = suggest(*b.build())
    names = [s.name.lower() for s in first]

    assert names == sorted(names)
    assert len({s.key for s in first}) == len(first)
    assert all(len(s.key) <= 200 for s in first)
