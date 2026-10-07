"""Reading a mapping's SQL text for the columns it reads (spec §6.14)."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse.lineage_sql import (
    ColumnRef,
    Unparsable,
    cast_null,
    is_group_safe,
    parse_branch,
    parse_expression,
)

PG = "postgresql"


@pytest.mark.parametrize(
    "sql, safe",
    [
        ("s.region", True),
        ("SUM(s.amount)", True),
        ("UPPER(s.region)", True),
        ("s.region || '-' || SUM(s.amount)", True),
        ("COUNT(*)", True),
        ("s.amount", False),
        ("SUM(s.amount) + s.amount", False),
        ("CASE WHEN SUM(s.amount) > 0 THEN s.amount END", False),
        ("CASE WHEN SUM(s.amount) > 0 THEN s.region END", True),
        ("SUM(s.amount) / s.qty", False),
    ],
)
def test_an_output_is_group_safe_only_when_every_bare_column_is_grouped(sql, safe):
    parts = parse_branch("s", "", "", "s.region", None, PG)

    assert is_group_safe(sql, parts, PG) is safe


def test_a_grouped_expression_covers_the_columns_inside_it():
    parts = parse_branch("s", "", "", "UPPER(s.region)", None, PG)

    assert is_group_safe("UPPER(s.region)", parts, PG)
    assert not is_group_safe("s.region", parts, PG)


def test_branch_references_resolve_table_aliases():
    parts = parse_branch(
        "crm c", "JOIN cb b ON b.id = c.id", "c.active", "c.region", "COUNT(b.id) > 1", PG
    )

    assert set(parts.refs) == set(
        refs(("cb", "id"), ("crm", "id"), ("crm", "active"), ("crm", "region"))
    )


def test_a_typed_null_uses_the_column_type():
    assert cast_null({"type": "string", "length": 20}, PG) == "CAST(NULL AS VARCHAR(20))"
    assert cast_null({"type": "decimal", "precision": 12, "scale": 2}, PG) == (
        "CAST(NULL AS DECIMAL(12, 2))"
    )
    assert cast_null({"type": "date"}, PG) == "CAST(NULL AS DATE)"


def refs(*pairs: tuple[str, str]) -> tuple[ColumnRef, ...]:
    return tuple(ColumnRef(t, c) for t, c in pairs)


def test_a_bare_column_is_a_direct_input():
    parsed = parse_expression("stg_crm_dbo_customer.first_name", "postgresql")
    assert parsed.is_column
    assert parsed.value == refs(("stg_crm_dbo_customer", "first_name"))
    assert parsed.uses == ()


def test_an_expression_reads_every_column_once_in_order():
    parsed = parse_expression("UPPER(c.first_name) || ' ' || c.last_name || c.first_name", "oracle")
    assert not parsed.is_column
    assert parsed.value == refs(("c", "first_name"), ("c", "last_name"))


def test_condition_columns_are_uses_and_result_columns_are_values():
    parsed = parse_expression("CASE WHEN o.status = 'X' THEN o.amount ELSE 0 END", "snowflake")
    assert parsed.value == refs(("o", "amount"))
    assert parsed.uses == refs(("o", "status"))


def test_a_column_may_be_both_value_and_use():
    parsed = parse_expression("CASE WHEN o.amount > 0 THEN o.amount END", "postgresql")
    assert parsed.value == refs(("o", "amount"))
    assert parsed.uses == refs(("o", "amount"))


def test_a_constant_reads_no_columns():
    parsed = parse_expression("'N/A'", "bigquery")
    assert parsed.value == () and parsed.uses == () and not parsed.is_column


def test_an_unqualified_column_has_no_table():
    assert parse_expression("amount * 2", "postgresql").value == refs((None, "amount"))


@pytest.mark.parametrize("sql", ["o.amount +", "SELECT 1 FROM t", "a b c", "((", "o.a, o.b"])
def test_text_that_is_not_one_expression_is_unparsable(sql):
    with pytest.raises(Unparsable):
        parse_expression(sql, "postgresql")


def test_names_differing_only_in_case_are_one_column():
    parsed = parse_expression("T.A || t.a", "postgresql")
    assert parsed.value == refs(("t", "a"))
