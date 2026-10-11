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
    kw.setdefault("description", "Who buys from us")
    return table(name, "dimension", columns=columns, scd_type=kw.pop("scd_type", 1), **kw)


def fact(dimension: DesignTable, name="fact_sales", **kw) -> DesignTable:
    columns = kw.pop("columns", None)
    if columns is None:
        columns = (
            col("customer_key", "fk", references_table_id=dimension.id),
            col("amount", "measure", additivity="additive", description="Net amount in SAR"),
        )
    kw.setdefault("description", "What we sold")
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
    staging = table("stg_erp_dbo_orders", "staging", layer="staging", status="source_removed")
    report = run(customers, fact(customers), staging)
    assert report.layer("mart").score is None
    assert report.layer("mart").grade is None
    assert report.layer("staging").score is not None and report.layer("staging").score < 100
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


def test_same_named_columns_share_a_data_type():
    customers = dim()
    sales = fact(customers)
    returns = fact(
        customers,
        "fact_returns",
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("amount", "measure", additivity="additive", data_type={"type": "decimal"}),
        ),
    )
    report = run(customers, sales, returns)
    assert not result(report, "same_name_same_type", name="fact_sales.amount").passed
    assert not result(report, "same_name_same_type", name="fact_returns.amount").passed
    assert result(report, "same_name_same_type", name="fact_sales.customer_key").passed


def test_same_named_columns_in_staging_are_not_compared():
    customers = dim()
    staging = table(
        "stg_erp_dbo_sales",
        "staging",
        layer="staging",
        columns=(col("amount", data_type={"type": "text"}),),
    )
    report = run(customers, fact(customers), staging)
    assert result(report, "same_name_same_type", name="fact_sales.amount").passed


def _mapped(sql: str, name="fact_sales", layer="core", **kw) -> DesignTable:
    return table(
        name,
        "fact",
        layer=layer,
        grain=GRAIN,
        columns=(col("amount", "measure", mappings=(MappingFact(None, "derived", sql),)),),
        **kw,
    )


def _staging(name="stg_erp_dbo_orders", columns=None, **kw) -> DesignTable:
    columns = columns if columns is not None else (col("amount"), col("order_id"))
    return table(name, "staging", layer="staging", columns=columns, **kw)


def test_inputs_come_only_from_the_layer_below():
    orders = _staging()
    core_orders = table("fact_orders", "fact", columns=(col("amount"),))
    ok = _mapped("stg_erp_dbo_orders.amount * 2")
    wrong = _mapped("fact_orders.amount", name="fact_wrong", layer="mart")
    skipping = _mapped("stg_erp_dbo_orders.amount", name="fact_skip", layer="mart")
    report = run(orders, core_orders, ok, wrong, skipping)
    assert result(report, "inputs_from_layer_below", name="fact_sales").passed
    assert result(report, "inputs_from_layer_below", name="fact_wrong").passed
    failed = result(report, "inputs_from_layer_below", name="fact_skip")
    assert not failed.passed and failed.severity == "error"
    assert "stg_erp_dbo_orders.amount (staging)" in failed.message


def test_a_lookups_dimension_is_not_an_input_but_its_natural_key_inputs_are():
    orders = _staging(columns=(col("customer_code"),))
    customers = dim()
    lookup = MappingFact(None, "lookup", lookup_inputs=("stg_erp_dbo_orders.customer_code",))
    sales = fact(
        customers,
        columns=(col("customer_key", "fk", references_table_id=customers.id, mappings=(lookup,)),),
    )
    report = run(orders, customers, sales)
    assert result(report, "inputs_from_layer_below", name="fact_sales").passed
    assert result(report, "inputs_exist", name="fact_sales").passed


def test_a_conformed_dimension_is_used_by_two_facts():
    shared = dim("dim_product", is_conformed=True)
    lonely = dim("dim_store", is_conformed=True)
    plain = dim("dim_region")
    facts = [
        fact(shared, "fact_sales"),
        fact(
            shared,
            "fact_returns",
            columns=(
                col("customer_key", "fk", references_table_id=shared.id),
                col("store_key", "fk", references_table_id=lonely.id),
            ),
        ),
    ]
    report = run(shared, lonely, plain, *facts)
    assert result(report, "conformed_dimension_shared", name="dim_product").passed
    assert not result(report, "conformed_dimension_shared", name="dim_store").passed
    assert not [
        f
        for f in report.findings
        if f.code == "conformed_dimension_shared" and f.object_name == "dim_region"
    ]


# --- traceability --------------------------------------------------------------------


def test_every_mapping_expression_join_and_filter_parses():
    orders = _staging()
    ok = _mapped("stg_erp_dbo_orders.amount")
    broken = _mapped("stg_erp_dbo_orders.amount +", name="fact_broken")
    bad_join = table(
        "fact_join",
        "fact",
        grain=GRAIN,
        branches=(DesignBranch(uuid.uuid4(), "erp", "stg_erp_dbo_orders", joins="JOIN ON"),),
    )
    report = run(orders, ok, broken, bad_join)
    assert result(report, "mapping_parses", name="fact_sales").passed
    assert not result(report, "mapping_parses", name="fact_broken").passed
    failed = result(report, "mapping_parses", name="fact_join")
    assert not failed.passed and "branch erp" in failed.message


def test_all_mapped_input_columns_exist():
    orders = _staging()
    ok = _mapped("stg_erp_dbo_orders.amount")
    gone_column = _mapped("stg_erp_dbo_orders.discount", name="fact_column")
    gone_table = _mapped("stg_erp_dbo_returns.amount", name="fact_table")
    branch = table(
        "fact_branch",
        "fact",
        grain=GRAIN,
        branches=(
            DesignBranch(
                uuid.uuid4(),
                "erp",
                "stg_erp_dbo_orders o",
                joins="JOIN stg_erp_dbo_lines l ON l.order_id = o.order_id",
            ),
        ),
    )
    report = run(orders, ok, gone_column, gone_table, branch)
    assert result(report, "inputs_exist", name="fact_sales").passed
    assert (
        "stg_erp_dbo_orders.discount" in result(report, "inputs_exist", name="fact_column").message
    )
    assert not result(report, "inputs_exist", name="fact_table").passed
    failed = result(report, "inputs_exist", name="fact_branch")
    assert not failed.passed and "stg_erp_dbo_lines" in failed.message


def test_a_table_with_nothing_mapped_is_not_scored_on_its_inputs():
    report = run(table("fact_empty", "fact", grain=GRAIN))
    for code in (
        "mapping_parses",
        "inputs_exist",
        "inputs_from_layer_below",
        "no_source_removed_inputs",
    ):
        assert not [f for f in report.findings if f.code == code]


def test_no_mapping_reads_a_source_removed_column():
    orders = _staging(columns=(col("amount"), col("discount", status="source_removed")))
    ok = _mapped("stg_erp_dbo_orders.amount")
    stale = _mapped("stg_erp_dbo_orders.amount - stg_erp_dbo_orders.discount", name="fact_stale")
    report = run(orders, ok, stale)
    assert result(report, "no_source_removed_inputs", name="fact_sales").passed
    failed = result(report, "no_source_removed_inputs", name="fact_stale")
    assert not failed.passed and failed.severity == "error"


def test_a_source_removed_staging_object_is_a_warning():
    kept = _staging()
    column_gone = _staging("stg_erp_dbo_lines", columns=(col("qty", status="source_removed"),))
    table_gone = _staging("stg_erp_dbo_old", status="source_removed")
    report = run(kept, column_gone, table_gone)
    assert result(report, "no_source_removed_inputs", name="stg_erp_dbo_orders").passed
    for name in ("stg_erp_dbo_lines", "stg_erp_dbo_old"):
        failed = result(report, "no_source_removed_inputs", name=name)
        assert not failed.passed and failed.severity == "warning"


def test_review_flags_are_resolved_by_overriding_the_flagged_field():
    clean = _staging()
    lossy = {"code": "lossy_type", "message": "NUMBER without precision became numeric(38,10)."}
    placeholder = {"code": "placeholder", "message": "The column name is not Latin."}
    flagged = _staging(
        "stg_erp_dbo_flagged",
        columns=(col("col_001", review_flags=(placeholder,)), col("total", review_flags=(lossy,))),
    )
    overridden = _staging(
        "stg_erp_dbo_fixed",
        columns=(
            col("customer_name", review_flags=(placeholder,), edited_fields=("name",)),
            col("total", review_flags=(lossy,), edited_fields=("data_type",)),
        ),
    )
    table_flag = _staging("stg_erp_dbo_tbl_007", review_flags=(placeholder,))
    report = run(clean, flagged, overridden, table_flag)
    assert result(report, "review_flags_resolved", name="stg_erp_dbo_orders").passed
    failed = result(report, "review_flags_resolved", name="stg_erp_dbo_flagged")
    assert not failed.passed and failed.severity == "warning"
    assert "col_001" in failed.message and "total" in failed.message
    assert result(report, "review_flags_resolved", name="stg_erp_dbo_fixed").passed
    assert not result(report, "review_flags_resolved", name="stg_erp_dbo_tbl_007").passed


# --- staging -------------------------------------------------------------------------


def test_staging_is_scored_only_on_naming_and_traceability():
    report = run(_staging(), _staging("stg_erp_dbo_lines", status="source_removed"))
    staging = [f for f in report.findings if f.layer == "staging"]
    assert staging
    categories = {product_registry.spec(f.code).category for f in staging}
    assert categories <= {"consistency", "traceability"}
    assert {f.code for f in staging if product_registry.spec(f.code).category == "consistency"} == {
        "naming_conventions"
    }
    assert report.layer("staging").score is not None
    assert report.score is None  # staging is never part of the Data Warehouse score


def test_staging_names_follow_the_staging_convention():
    ok = _staging()
    no_prefix = _staging("erp_dbo_orders")
    shouting = _staging("stg_erp_dbo_lines", columns=(col("Qty"),))
    too_long = _staging("stg_" + "x" * 70)
    report = run(ok, no_prefix, shouting, too_long)
    assert result(report, "naming_conventions", name="stg_erp_dbo_orders").passed
    assert not result(report, "naming_conventions", name="erp_dbo_orders").passed
    assert "lower case" in result(report, "naming_conventions", name="stg_erp_dbo_lines").message
    assert "63-byte" in result(report, "naming_conventions", name="stg_" + "x" * 70).message


def test_generated_tables_are_exempt_from_the_new_mapping_checks():
    dates = table(
        "dim_date",
        "generated",
        columns=(col("date_key", "sk", mappings=(MappingFact(None, "derived", "nonsense +"),)),),
    )
    report = run(dates)
    for code in (
        "mapping_parses",
        "inputs_exist",
        "inputs_from_layer_below",
        "no_source_removed_inputs",
    ):
        assert not [f for f in report.findings if f.code == code]


# --- documentation -------------------------------------------------------------------


def test_every_dw_table_has_a_description():
    customers = dim()
    bare = dim("dim_store", description="")
    report = run(customers, bare)
    assert result(report, "table_has_description", name="dim_customer").passed
    failed = result(report, "table_has_description", name="dim_store")
    assert not failed.passed and failed.severity == "info"


def test_every_measure_has_a_description():
    customers = dim()
    sales = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("amount", "measure", additivity="additive", description="Net amount"),
            col("qty", "measure", additivity="additive"),
        ),
    )
    report = run(customers, sales)
    assert result(report, "measure_has_description", name="fact_sales.amount").passed
    assert not result(report, "measure_has_description", name="fact_sales.qty").passed
    assert not [
        f
        for f in report.findings
        if f.code == "measure_has_description" and f.object_name == "fact_sales.customer_key"
    ]


# --- performance readiness -----------------------------------------------------------


def test_surrogate_keys_use_integer_types():
    ok = dim()
    uuid_key = dim(
        "dim_store",
        columns=(col("store_key", "sk", data_type={"type": "uuid"}), col("store_code", "nk")),
    )
    dates = table("dim_date", "generated", columns=(col("date_key", "sk"),))
    report = run(ok, uuid_key, dates)
    assert result(report, "surrogate_key_integer", name="dim_customer.customer_key").passed
    assert result(report, "surrogate_key_integer", name="dim_date.date_key").passed
    failed = result(report, "surrogate_key_integer", name="dim_store.store_key")
    assert not failed.passed and failed.severity == "warning"


def test_no_free_text_on_fact_tables():
    customers = dim()
    text = {"type": "string", "length": 200}
    sales = fact(
        customers,
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            col("amount", "measure", additivity="additive"),
            col("comment", "attribute", data_type=text),
            col("invoice_no", "degenerate_dimension", data_type=text),
        ),
    )
    report = run(customers, sales)
    assert result(report, "fact_no_free_text", name="fact_sales.amount").passed
    assert not result(report, "fact_no_free_text", name="fact_sales.comment").passed
    assert not [
        f
        for f in report.findings
        if f.code == "fact_no_free_text" and f.object_name == "fact_sales.invoice_no"
    ]


def test_fact_tables_wider_than_60_columns():
    customers = dim()
    narrow = fact(customers)
    wide = fact(
        customers,
        "fact_wide",
        columns=(
            col("customer_key", "fk", references_table_id=customers.id),
            *(col(f"m{i}", "measure", additivity="additive") for i in range(60)),
        ),
    )
    report = run(customers, narrow, wide)
    assert result(report, "fact_width", name="fact_sales").passed
    failed = result(report, "fact_width", name="fact_wide")
    assert not failed.passed and failed.severity == "info"


# --- privacy -------------------------------------------------------------------------


def test_pii_derived_columns_reaching_the_mart_are_flagged():
    customers = dim(layer="mart")
    clean = dim(
        "dim_store", layer="mart", columns=(col("store_key", "sk"), col("store_code", "nk"))
    )
    exposed = dim(
        "dim_person",
        layer="mart",
        columns=(col("person_key", "sk"), col("national_id", "nk", pii_derived=True)),
    )
    core = dim("dim_core", columns=(col("core_key", "sk"), col("email", "nk", pii_derived=True)))
    report = run(customers, clean, exposed, core)
    assert result(report, "pii_in_mart", name="dim_store").passed
    failed = result(report, "pii_in_mart", name="dim_person")
    assert not failed.passed and failed.severity == "info" and "national_id" in failed.message
    assert not [f for f in report.findings if f.code == "pii_in_mart" and f.layer == "core"]


def test_every_check_in_the_spec_table_is_registered_with_its_layers_and_severity():
    expected = {
        "naming_conventions": ("warning", ("staging", "core", "mart")),
        "same_name_same_type": ("warning", ("core", "mart")),
        "inputs_from_layer_below": ("error", ("staging", "core", "mart")),
        "conformed_dimension_shared": ("info", ("core", "mart")),
        "mapping_parses": ("error", ("staging", "core", "mart")),
        "inputs_exist": ("error", ("staging", "core", "mart")),
        "no_source_removed_inputs": ("error", ("staging", "core", "mart")),
        "review_flags_resolved": ("warning", ("staging",)),
        "table_has_description": ("info", ("core", "mart")),
        "measure_has_description": ("info", ("core", "mart")),
        "surrogate_key_integer": ("warning", ("core", "mart")),
        "fact_no_free_text": ("warning", ("core", "mart")),
        "fact_width": ("info", ("core", "mart")),
        "pii_in_mart": ("info", ("mart",)),
    }
    for code, (severity, layers) in expected.items():
        spec = product_registry.spec(code)
        assert spec is not None, code
        assert (spec.severity, spec.layers) == (severity, layers), code


def test_staging_mappings_read_the_source_and_are_not_checked_as_dw_inputs():
    generated = _staging(
        columns=(col("amount", mappings=(MappingFact(None, "direct", '"dbo"."orders"."amount"'),)),)
    )
    report = run(generated)
    assert not [f for f in report.findings if f.code in ("inputs_exist", "inputs_from_layer_below")]
    assert result(report, "mapping_parses", name="stg_erp_dbo_orders").passed


def test_a_cte_in_a_branch_is_not_a_missing_input():
    orders = _staging()
    branch = table(
        "fact_branch",
        "fact",
        grain=GRAIN,
        branches=(
            DesignBranch(
                uuid.uuid4(),
                "erp",
                "stg_erp_dbo_orders o",
                joins="JOIN recent r ON r.order_id = o.order_id",
            ),
        ),
    )
    report = run(orders, branch)
    failed = result(report, "inputs_exist", name="fact_branch")
    assert "recent (in" not in failed.message  # only the column read through it is reported


def test_an_unset_scale_is_the_same_type_as_scale_zero():
    customers = dim()
    a = fact(
        customers,
        columns=(
            col(
                "price",
                "measure",
                additivity="additive",
                data_type={"type": "decimal", "precision": 10, "scale": None},
            ),
        ),
    )
    b = fact(
        customers,
        "fact_b",
        columns=(
            col(
                "price",
                "measure",
                additivity="additive",
                data_type={"type": "decimal", "precision": 10, "scale": 0},
            ),
        ),
    )
    report = run(customers, a, b)
    assert result(report, "same_name_same_type", name="fact_sales.price").passed
