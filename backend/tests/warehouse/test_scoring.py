"""The scoring engine and the first checks (spec §6.11), on hand-built designs: no database.

Each check has a pass and a fail fixture; the formula, weights, grades and error cap are
tested on known designs with known scores.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from dawam.modules.warehouse import NamingRules
from dawam.modules.warehouse.score_checks import registry as product_registry
from dawam.modules.warehouse.scoring import (
    PASS,
    CheckRegistry,
    Design,
    DesignBranch,
    DesignColumn,
    DesignTable,
    MappingFact,
    ScoreReport,
    evaluate,
    fail,
    grade_of,
)

# --- builders ------------------------------------------------------------------------


def col(name="c", role="attribute", table_id=None, **kw) -> DesignColumn:
    """A column that is mapped the way its role asks (a lookup for a foreign key, nothing
    for a system key), unless ``mappings`` says otherwise."""
    system = role in ("sk", "audit")
    default = () if system else (MappingFact(None, "lookup" if role == "fk" else "direct"),)
    return DesignColumn(
        id=uuid.uuid4(),
        table_id=table_id or uuid.uuid4(),
        name=name,
        data_type=kw.pop("data_type", {"type": "integer"}),
        role=role,
        needs_no_mapping=kw.pop("needs_no_mapping", system),
        mappings=kw.pop("mappings", default),
        **kw,
    )


def table(name="fact_sales", kind="fact", layer="core", columns=(), **kw) -> DesignTable:
    t = DesignTable(id=uuid.uuid4(), layer=layer, name=name, kind=kind, **kw)
    return replace(
        t,
        columns=tuple(replace(c, table_id=t.id) for c in columns),
    )


def design(*tables: DesignTable, platform="postgresql") -> Design:
    return Design(platform=platform, naming_rules=NamingRules(), tables=tuple(tables))


def run(*tables: DesignTable) -> ScoreReport:
    return evaluate(design(*tables), product_registry)


def result(report: ScoreReport, code: str, *, name: str | None = None):
    """The finding of check ``code`` (on the object ``name``, if there are several)."""
    found = [
        f for f in report.findings if f.code == code and (name is None or f.object_name == name)
    ]
    assert len(found) == 1, found
    return found[0]


def codes_failed(report: ScoreReport) -> set[str]:
    return {f.code for f in report.failed}


GRAIN = "One row per order line"


def dim(name="dim_customer", **kw) -> DesignTable:
    columns = kw.pop("columns", None)
    if columns is None:
        columns = (col("customer_key", "sk"), col("customer_code", "nk"))
    return table(name, "dimension", columns=columns, scd_type=kw.pop("scd_type", 1), **kw)


def fact(dimension: DesignTable, name="fact_sales", **kw) -> DesignTable:
    columns = kw.pop("columns", None)
    if columns is None:
        columns = (
            col("customer_key", "fk", references_table_id=dimension.id),
            col("amount", "measure", additivity="additive"),
        )
    return table(name, "fact", columns=columns, grain=kw.pop("grain", GRAIN), **kw)


# --- the engine ----------------------------------------------------------------------


def test_a_clean_design_scores_100_and_an_A():
    customers = dim()
    report = run(customers, fact(customers))
    assert report.failed == []
    assert report.score == 100
    assert report.grade == "A"
    assert not report.capped


def test_weights_are_error_10_warning_3_info_1():
    registry = CheckRegistry()
    for code, severity, ok in (("e", "error", True), ("w", "warning", True), ("i", "info", False)):
        registry.table_check(
            code, category="completeness", severity=severity, layers=("core",), hint=""
        )(lambda t, d, ok=ok: PASS if ok else fail("no"))
    report = evaluate(design(table("t")), registry)
    # 13 of 14 points pass.
    assert report.score == pytest.approx(92.86)
    assert report.tables[0].score == pytest.approx(92.86)


def test_grades_are_A_90_B_80_C_70_D_60_F_below():
    assert [grade_of(s) for s in (100, 90, 89.99, 80, 79.99, 70, 69.99, 60, 59.99, 0)] == [
        "A",
        "A",
        "B",
        "B",
        "C",
        "C",
        "D",
        "D",
        "F",
        "F",
    ]


def test_an_unresolved_error_caps_the_grade_at_c():
    assert grade_of(95, has_errors=True) == "C"
    assert grade_of(85, has_errors=True) == "C"
    assert grade_of(65, has_errors=True) == "D"  # a cap only lowers
    customers = dim()
    # One failing error among many passing warnings keeps the score high, but not the grade.
    wide = tuple(col(f"m{i}", "measure", additivity="additive") for i in range(30))
    many = fact(
        customers,
        columns=(col("customer_key", "fk", references_table_id=customers.id), *wide),
        grain=None,
    )
    report = run(customers, many)
    assert report.score is not None and report.score >= 90
    assert report.grade == "C" and report.capped


def test_a_layer_with_no_tables_is_not_scored_and_staging_is_apart():
    customers = dim()
    report = run(customers, fact(customers), table("stg_orders", "staging", layer="staging"))
    assert report.layer("mart").score is None
    assert report.layer("mart").grade is None
    assert report.layer("staging").score is None  # nothing in staging is checked yet
    assert report.layer("core").score == 100
    assert report.score == 100


def test_nothing_to_score_is_none_not_zero_or_100():
    report = run()
    assert report.score is None and report.grade is None
    assert all(layer.score is None for layer in report.layers)


def test_staging_findings_never_count_towards_the_dw_score():
    registry = CheckRegistry()
    registry.table_check(
        "stg", category="traceability", severity="error", layers=("staging",), hint=""
    )(lambda t, d: fail("bad"))
    registry.table_check(
        "ok", category="traceability", severity="error", layers=("core",), hint=""
    )(lambda t, d: PASS)
    report = evaluate(
        design(table("stg_a", "staging", layer="staging"), table("dim_a", "dimension")), registry
    )
    assert report.layer("staging").score == 0
    assert report.score == 100 and report.grade == "A"


def test_a_layer_score_averages_its_tables_weighted_by_column_count():
    registry = CheckRegistry()
    registry.table_check(
        "has_desc", category="documentation", severity="info", layers=("core",), hint=""
    )(lambda t, d: PASS if t.description else fail("none"))
    wide = table(
        "wide", "dimension", columns=tuple(col(f"c{i}") for i in range(3)), description="x"
    )
    narrow = table("narrow", "dimension", columns=(col("a"),))
    report = evaluate(design(wide, narrow), registry)
    # (100 * 3 + 0 * 1) / 4
    assert report.layer("core").score == 75
    # The Data Warehouse score is the weighted pass percentage over findings: 1 of 2.
    assert report.score == 50


def test_a_table_score_includes_its_columns_findings():
    customers = dim()
    unspecified = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("amount", "measure"),
        ),
    )
    report = run(customers, unspecified)
    # warning 3 of the table's 10 + 10 + 3 (+ naming 3 passes): the measure check fails.
    [row] = [t for t in report.tables if t.name == "fact_sales"]
    assert row.score is not None and row.score < 100


def test_a_disabled_check_is_left_out_and_listed():
    customers = dim()
    broken = fact(customers, grain=None)
    enabled = evaluate(design(customers, broken), product_registry)
    disabled = evaluate(design(customers, broken), product_registry, disabled={"fact_has_grain"})
    assert "fact_has_grain" in codes_failed(enabled)
    assert "fact_has_grain" not in {f.code for f in disabled.findings}
    assert disabled.disabled == ("fact_has_grain",)
    assert disabled.score == 100 and not disabled.capped


def test_a_check_only_scores_objects_in_its_layers():
    registry = CheckRegistry()

    @registry.check("rogue", category="completeness", severity="error", layers=("mart",), hint="")
    def rogue(d, spec):  # a sloppy check that reports a Core table
        from dawam.modules.warehouse.scoring import Finding

        for t in d.tables:
            yield Finding("rogue", "error", t.layer, "table", t.id, t.id, t.name, False)

    report = evaluate(
        design(table("a", "dimension"), table("b", "dimension", layer="mart")), registry
    )
    assert [f.object_name for f in report.findings] == ["b"]


def test_registering_a_code_twice_is_refused():
    registry = CheckRegistry()
    registry.table_check("x", category="completeness", severity="info", layers=("core",), hint="")(
        lambda t, d: PASS
    )
    with pytest.raises(ValueError):
        registry.table_check(
            "x", category="completeness", severity="info", layers=("core",), hint=""
        )(lambda t, d: PASS)


# --- completeness --------------------------------------------------------------------


def test_fact_has_grain():
    customers = dim()
    assert result(run(customers, fact(customers)), "fact_has_grain").passed
    for grain in (None, "", "   "):
        assert not result(run(customers, fact(customers, grain=grain)), "fact_has_grain").passed


def test_column_covered_without_branches_needs_a_table_level_mapping():
    mapped = col(
        "amount", "measure", additivity="additive", mappings=(MappingFact(None, "direct"),)
    )
    unmapped = col(
        "net", "measure", additivity="additive", mappings=(MappingFact(None, "unmapped"),)
    )
    never = col("tax", "measure", additivity="additive", mappings=())
    system = col("sales_key", "sk", needs_no_mapping=True)
    report = run(table(columns=(mapped, unmapped, never, system), grain=GRAIN))
    by_name = {
        f.object_name.split(".")[1]: f.passed for f in report.findings if f.code == "column_covered"
    }
    assert by_name == {"amount": True, "net": False, "tax": False, "sales_key": True}


def test_column_covered_in_every_branch():
    a, b = uuid.uuid4(), uuid.uuid4()
    branches = (DesignBranch(a, "crm", "stg_crm"), DesignBranch(b, "erp", "stg_erp"))
    both = col("both", mappings=(MappingFact(a, "direct"), MappingFact(b, "not_in_branch")))
    one = col("one", mappings=(MappingFact(a, "direct"),))
    open_ = col("open", mappings=(MappingFact(a, "direct"), MappingFact(b, "unmapped")))
    report = run(table("dim_x", "dimension", columns=(both, one, open_), branches=branches))
    by_name = {
        f.object_name.split(".")[1]: f for f in report.findings if f.code == "column_covered"
    }
    assert by_name["both"].passed
    assert not by_name["one"].passed and "erp" in by_name["one"].message
    assert not by_name["open"].passed


def test_generated_tables_are_exempt_from_mapping_checks():
    date = table("dim_date", "generated", columns=(col("date_key", "sk"),))
    assert not [f for f in run(date).findings if f.code == "column_covered"]


def _aggregate(group_by: str, expression: str) -> DesignTable:
    branch = DesignBranch(uuid.uuid4(), "main", "stg_orders", group_by=group_by)
    amount = col("amount", "measure", additivity="additive")
    amount = replace(amount, mappings=(MappingFact(branch.id, "derived", expression),))
    return table(
        "fact_daily", columns=(amount,), grain=GRAIN, is_aggregate=True, branches=(branch,)
    )


def test_aggregate_non_aggregated_columns_must_be_grouped():
    assert result(
        run(_aggregate("stg_orders.o_day", "SUM(stg_orders.total)")), "aggregate_group_by"
    ).passed
    assert result(
        run(_aggregate("stg_orders.o_day", "stg_orders.o_day")), "aggregate_group_by"
    ).passed
    bad = result(run(_aggregate("stg_orders.o_day", "stg_orders.total")), "aggregate_group_by")
    assert not bad.passed and "amount" in bad.message


def test_group_by_applies_to_aggregate_tables_only():
    customers = dim()
    assert not [
        f for f in run(customers, fact(customers)).findings if f.code == "aggregate_group_by"
    ]


# --- dimensional modeling ------------------------------------------------------------


def test_dimension_has_surrogate_and_natural_key():
    assert result(run(dim()), "dimension_has_keys").passed
    no_nk = dim(columns=(col("customer_key", "sk"),))
    no_sk = dim(columns=(col("customer_code", "nk"),))
    assert "natural" in result(run(no_nk), "dimension_has_keys").message
    assert "surrogate" in result(run(no_sk), "dimension_has_keys").message


def test_fact_has_a_dimension_fk():
    customers = dim()
    assert result(run(customers, fact(customers)), "fact_has_dimension_fk").passed
    lonely = fact(customers, columns=(col("amount", "measure", additivity="additive"),))
    assert not result(run(customers, lonely), "fact_has_dimension_fk").passed


def test_a_fk_to_a_missing_table_does_not_count():
    gone = uuid.uuid4()
    orphan = fact(dim(), columns=(col("customer_key", "fk", references_table_id=gone),))
    assert not result(run(orphan), "fact_has_dimension_fk").passed


def test_no_fact_to_fact_fk():
    customers = dim()
    other = fact(customers, name="fact_other")
    linked = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("other_key", "fk", references_table_id=other.id),
        ),
    )
    report = run(customers, other, linked)
    assert result(report, "no_fact_to_fact_fk", name="fact_sales.customer_key").passed
    assert not result(report, "no_fact_to_fact_fk", name="fact_sales.other_key").passed


def test_a_fact_with_time_based_measures_links_to_a_date_dimension():
    customers = dim()
    date = table("dim_date", "generated", columns=(col("date_key", "sk"),))
    timed = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col(
                "shipped_at", "measure", additivity="non_additive", data_type={"type": "timestamp"}
            ),
        ),
    )
    assert not result(run(customers, timed), "time_fact_has_date_dimension").passed
    linked = replace(
        timed,
        columns=(
            *timed.columns,
            col("date_key", "fk", table_id=timed.id, references_table_id=date.id),
        ),
    )
    assert result(run(customers, date, linked), "time_fact_has_date_dimension").passed
    # A fact without anything time-based is not asked for one.
    assert not [
        f
        for f in run(customers, fact(customers)).findings
        if f.code == "time_fact_has_date_dimension"
    ]


def test_snapshot_facts_count_as_time_based():
    customers = dim()
    snapshot = fact(customers, fact_type="periodic_snapshot")
    assert not result(run(customers, snapshot), "time_fact_has_date_dimension").passed


def test_every_measure_declares_additivity():
    customers = dim()
    mixed = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("amount", "measure", additivity="additive"),
            col("ratio", "measure"),
        ),
    )
    report = run(customers, mixed)
    assert result(report, "measure_has_additivity", name="fact_sales.amount").passed
    assert not result(report, "measure_has_additivity", name="fact_sales.ratio").passed


def test_every_dimension_declares_an_scd_type():
    assert result(run(dim()), "dimension_has_scd_type").passed
    assert not result(run(dim(scd_type=None)), "dimension_has_scd_type").passed
    assert result(run(dim(scd_type=0)), "dimension_has_scd_type").passed


def test_snowflaking_deeper_than_one_level_is_flagged():
    country = dim("dim_country", columns=(col("country_key", "sk"), col("country_code", "nk")))
    region = dim(
        "dim_region",
        columns=(
            col("region_key", "sk"),
            col("region_code", "nk"),
            col("country_key", "fk", references_table_id=country.id),
        ),
    )
    customer = dim(
        "dim_customer",
        columns=(
            col("customer_key", "sk"),
            col("customer_code", "nk"),
            col("region_key", "fk", references_table_id=region.id),
        ),
    )
    report = run(country, region, customer)
    assert result(report, "no_deep_snowflake", name="dim_customer").passed is False
    assert result(report, "no_deep_snowflake", name="dim_region").passed  # one level is allowed
    assert result(report, "no_deep_snowflake", name="dim_country").passed


def test_snowflake_walk_survives_a_cycle():
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    a = replace(dim("dim_a", columns=(col("a_key", "sk"), col("a_code", "nk"))), id=a_id)
    b = replace(dim("dim_b", columns=(col("b_key", "sk"), col("b_code", "nk"))), id=b_id)
    a = replace(a, columns=(*a.columns, col("b_key", "fk", references_table_id=b_id)))
    b = replace(b, columns=(*b.columns, col("a_key", "fk", references_table_id=a_id)))
    assert run(a, b).score is not None


def test_a_core_fact_fk_to_a_dimension_is_mapped_as_a_lookup():
    customers = dim()
    plain = fact(
        customers,
        columns=(
            col(
                "customer_key",
                "fk",
                references_table_id=customers.id,
                mappings=(MappingFact(None, "direct"),),
            ),
        ),
    )
    looked_up = fact(
        customers,
        columns=(
            col(
                "customer_key",
                "fk",
                references_table_id=customers.id,
                mappings=(MappingFact(None, "lookup"),),
            ),
        ),
    )
    assert not result(run(customers, plain), "fact_fk_is_lookup").passed
    assert result(run(customers, looked_up), "fact_fk_is_lookup").passed


def test_the_lookup_check_applies_to_core_only():
    customers = dim(layer="mart")
    mart_fact = fact(customers, layer="mart")
    assert not [f for f in run(customers, mart_fact).findings if f.code == "fact_fk_is_lookup"]


def test_a_bridge_has_a_group_key_and_two_fks():
    customers, accounts = dim(), dim("dim_account")
    good = table(
        "bridge_ca",
        "bridge",
        columns=(
            col("bridge_ca_group_key", "sk"),
            col("customer_key", "fk", references_table_id=customers.id),
            col("account_key", "fk", references_table_id=accounts.id),
        ),
    )
    thin = table(
        "bridge_x",
        "bridge",
        columns=(
            col("bridge_x_group_key", "sk"),
            col("customer_key", "fk", references_table_id=customers.id),
        ),
    )
    report = run(customers, accounts, good, thin)
    assert result(report, "bridge_has_keys", name="bridge_ca").passed
    assert not result(report, "bridge_has_keys", name="bridge_x").passed


# --- consistency (naming, from the naming module) --------------------------------------


def test_naming_conventions_are_scored_through_the_naming_module():
    ok = dim("dim_customer")
    wrong_prefix = dim("customer")
    shouting = dim("dim_x", columns=(col("X_KEY", "sk"), col("x_code", "nk")))
    system = dim("dim_y", columns=(col("Y_KEY", "sk", is_system=True), col("y_code", "nk")))
    report = run(ok, wrong_prefix, shouting, system)
    assert result(report, "naming_conventions", name="dim_customer").passed
    assert not result(report, "naming_conventions", name="customer").passed
    assert not result(report, "naming_conventions", name="dim_x").passed
    assert result(report, "naming_conventions", name="dim_y").passed


def test_every_registered_check_has_a_hint_and_a_known_category():
    categories = {
        "completeness",
        "dimensional_modeling",
        "consistency",
        "traceability",
        "documentation",
        "performance_readiness",
        "privacy",
    }
    assert len({s.code for s in product_registry.specs}) == len(product_registry.specs)
    for spec in product_registry.specs:
        assert spec.hint and spec.category in categories and spec.title
