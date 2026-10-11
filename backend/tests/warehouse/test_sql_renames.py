"""Respelling renamed tables and columns in SQL text, in place (spec §6.10, propagation)."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse.lineage_sql import (
    Renames,
    branch_reads,
    expression_reads,
    rename_in_branch,
    rename_in_expression,
    rename_in_sql,
)

PG = "postgresql"
COLUMN = Renames(tables={}, columns={("stg_c", "name"): "full_name"})
TABLE = Renames(tables={"stg_c": "stg_customer"}, columns={})


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("stg_c.name", "stg_c.full_name"),
        # Only the identifier changes: case, layout and functions stay as written.
        ("upper( stg_c.name )  ||  '-'", "upper( stg_c.full_name )  ||  '-'"),
        ('"STG_C"."name"', '"STG_C"."full_name"'),
        (
            "CASE WHEN stg_c.name IS NULL THEN 'n/a' ELSE stg_c.name END",
            "CASE WHEN stg_c.full_name IS NULL THEN 'n/a' ELSE stg_c.full_name END",
        ),
        ("stg_o.name", "stg_o.name"),
        ("stg_c.named", "stg_c.named"),
        ("not ) sql", "not ) sql"),
    ],
)
def test_a_column_rename_respells_only_that_column(sql, expected):
    assert rename_in_expression(sql, PG, COLUMN) == expected


def test_a_table_rename_respells_the_qualifier():
    assert rename_in_expression("stg_c.id + stg_o.id", PG, TABLE) == "stg_customer.id + stg_o.id"


def test_a_branch_follows_its_aliases_and_keeps_each_part_apart():
    parts = {
        "driving_input": "stg_o AS o",
        "joins": "JOIN stg_c c ON c.name = o.customer_name",
        "filters": "stg_c.name <> ''",
        "group_by": None,
        "having": None,
    }

    assert rename_in_branch(parts, PG, COLUMN) == parts | {
        "joins": "JOIN stg_c c ON c.full_name = o.customer_name",
        "filters": "stg_c.full_name <> ''",
    }
    assert rename_in_branch(parts, PG, TABLE) == parts | {
        "joins": "JOIN stg_customer c ON c.name = o.customer_name",
        "filters": "stg_customer.name <> ''",
    }


def test_a_kpi_formula_is_respelled_in_its_layer_schema_only():
    renames = Renames(
        tables={"fact_sales": "fact_sale"},
        columns={("fact_sales", "amount"): "net_amount"},
        schema="core",
    )

    assert rename_in_sql("SELECT SUM(f.amount) FROM core.fact_sales f", PG, renames) == (
        "SELECT SUM(f.net_amount) FROM core.fact_sale f"
    )
    assert rename_in_sql("SELECT SUM(amount) FROM fact_sales", PG, renames) == (
        "SELECT SUM(net_amount) FROM fact_sale"
    )
    assert rename_in_sql("SELECT SUM(amount) FROM mart.fact_sales", PG, renames) == (
        "SELECT SUM(amount) FROM mart.fact_sales"
    )


def test_reads_finds_a_removed_table_or_column():
    removed = Renames(tables={}, columns={("stg_c", "name"): "name"})

    assert expression_reads("COALESCE(stg_c.name, '')", PG, removed)
    assert not expression_reads("stg_c.id", PG, removed)
    assert branch_reads(
        {"driving_input": "stg_c", "joins": "", "filters": "stg_c.name <> ''"}, PG, removed
    )
