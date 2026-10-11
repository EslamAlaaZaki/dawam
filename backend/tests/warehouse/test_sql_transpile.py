"""Moving stored SQL between target platforms' dialects (spec §6.9 "Dialect", story 87)."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse.sql_transpile import transpile


def test_an_expression_is_respelled_in_the_new_dialect():
    result = transpile("expression", "ISNULL(b, 0) + GETDATE()", "sqlserver", "postgresql")

    assert result.manual is None
    assert result.changed
    assert result.sql == "COALESCE(b, 0) + CURRENT_TIMESTAMP"


def test_text_both_dialects_spell_the_same_is_left_alone():
    result = transpile("expression", "COALESCE(a, 0)", "postgresql", "sqlserver")

    assert result.sql == "COALESCE(a, 0)"
    assert not result.changed


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("filter", "x ILIKE 'a%' AND DATEADD(day, 1, d) > GETDATE()", "CURRENT_TIMESTAMP"),
        ("joins", "LEFT JOIN b ON a.id = b.id", "LEFT JOIN b ON a.id = b.id"),
        ("group_by", "ISNULL(a, 0), b", "COALESCE(a, 0), b"),
        ("having", "COUNT(*) > 1", "COUNT(*) > 1"),
        ("table", "[dbo].[orders]", '"dbo"."orders"'),
        ("query", "SELECT TOP 5 a FROM [t]", 'SELECT a FROM "t" LIMIT 5'),
    ],
)
def test_every_kind_of_stored_fragment_is_translated(kind, text, expected):
    result = transpile(kind, text, "sqlserver", "postgresql")

    assert result.manual is None, result.manual
    assert result.sql is not None
    assert expected in result.sql


def test_what_the_target_cannot_express_is_flagged_manual():
    text = "STRING_AGG(x, ',' ORDER BY y)"

    result = transpile("expression", text, "postgresql", "bigquery")

    assert result.sql is None
    assert result.manual
    assert result.manual.startswith("postgresql to bigquery")


def test_text_that_does_not_parse_is_flagged_manual():
    result = transpile("expression", "CASE WHEN", "postgresql", "oracle")

    assert result.sql is None
    assert result.manual


def test_more_than_one_expression_is_flagged_manual():
    result = transpile("expression", "a, b", "postgresql", "oracle")

    assert result.sql is None
    assert result.manual
