"""Naming-convention validation (spec story 96): pure checks, then shown on the model."""

from __future__ import annotations

from dawam.modules.warehouse import NamingRules
from dawam.modules.warehouse.naming import check_column_name, check_table_name
from tests.warehouse.test_core_mart_model import (
    base,
    column,
    dimension,
    fact,
    get_table,
    warehouse,  # noqa: F401
)

RULES = NamingRules()


def codes(violations) -> list[str]:
    return [v.code for v in violations]


def test_a_conforming_table_name_has_no_violations():
    assert check_table_name(RULES, layer="core", kind="fact", name="fact_sales") == []
    assert check_table_name(RULES, layer="mart", kind="dimension", name="dim_customer") == []
    assert check_table_name(RULES, layer="mart", kind="bridge", name="bridge_x") == []


def test_a_missing_prefix_is_flagged_with_the_expected_prefix():
    (violation,) = check_table_name(RULES, layer="core", kind="fact", name="sales")
    assert (violation.code, violation.expected) == ("prefix", "fact_")


def test_the_prefix_follows_the_kind():
    assert codes(check_table_name(RULES, layer="core", kind="dimension", name="fact_x")) == [
        "prefix"
    ]


def test_case_style_applies_to_tables_and_columns():
    assert codes(check_table_name(RULES, layer="core", kind="fact", name="Fact_Sales")) == [
        "case_style"
    ]
    assert codes(check_column_name(RULES, layer="core", name="OrderId")) == ["case_style"]
    upper = NamingRules(case_style="upper", fact_prefix="FACT_")
    assert check_table_name(upper, layer="core", kind="fact", name="FACT_SALES") == []
    assert codes(check_table_name(upper, layer="core", kind="fact", name="fact_sales")) == [
        "case_style"
    ]


def test_prefix_is_compared_regardless_of_case_and_an_empty_prefix_asks_nothing():
    upper = NamingRules(case_style="upper", dimension_prefix="dim_")
    assert check_table_name(upper, layer="core", kind="dimension", name="DIM_X") == []
    none = NamingRules(fact_prefix="")
    assert check_table_name(none, layer="core", kind="fact", name="sales") == []


def test_other_kinds_need_no_prefix_and_staging_and_system_columns_are_exempt():
    assert check_table_name(RULES, layer="core", kind="generated", name="date_dim") == []
    assert check_table_name(RULES, layer="staging", kind="staging", name="RawOrders") == []
    assert check_column_name(RULES, layer="staging", name="OrderId") == []
    assert check_column_name(RULES, layer="core", name="X", is_system=True) == []


# --- shown on the objects ------------------------------------------------------------


def test_violations_are_shown_on_tables_and_columns(warehouse):  # noqa: F811
    detail = fact(warehouse, name="Sales")
    assert [v["code"] for v in detail["naming_violations"]] == ["case_style", "prefix"]
    col = column(
        warehouse, detail["id"], name="OrderId", data_type={"type": "integer"}, role="attribute"
    )
    assert [v["code"] for v in col["naming_violations"]] == ["case_style"]
    again = get_table(warehouse, detail["id"])
    assert again["columns"][0]["naming_violations"][0]["expected"] == "lower"
    listed = warehouse.client("viewer").get(f"{base(warehouse)}/tables").json()["items"]
    assert listed[0]["naming_violation_count"] == 2


def test_a_conforming_model_is_clean(warehouse):  # noqa: F811
    detail = dimension(warehouse, name="dim_customer")
    assert detail["naming_violations"] == []
    assert all(c["naming_violations"] == [] for c in detail["columns"])


def test_changing_the_naming_rules_changes_the_violations(warehouse):  # noqa: F811
    detail = fact(warehouse, name="fact_sales")
    assert detail["naming_violations"] == []
    setup = base(warehouse)
    current = warehouse.client("editor").get(setup).json()
    response = warehouse.client("editor").patch(
        setup, json={"version": current["version"], "naming_rules": {"fact_prefix": "fct_"}}
    )
    assert response.status_code == 200, response.text
    assert [v["code"] for v in get_table(warehouse, detail["id"])["naming_violations"]] == [
        "prefix"
    ]
