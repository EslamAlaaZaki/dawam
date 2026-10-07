"""The pure rules of relationship inference (spec §6.6): name similarity, type
compatibility, the score, and JOIN conditions parsed from view and routine text."""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.inference import (
    JoinSide,
    name_similarity,
    routine_joins,
    score,
    type_match,
)


@pytest.mark.parametrize(
    ("from_column", "to_table", "to_column", "expected"),
    [
        ("customer_id", "customers", "id", 1.0),
        ("customerId", "customers", "id", 1.0),
        ("cust_id", "customers", "id", 0.8),
        ("cust_no", "customers", "cust_no", 0.9),
        ("cust_no", "customers", "id", 0.0),
        ("category_id", "categories", "id", 1.0),
        ("id", "customers", "id", 0.0),
        ("notes", "customers", "id", 0.0),
        ("branch_code", "branches", "branch_code", 0.9),
    ],
)
def test_name_similarity(from_column, to_table, to_column, expected):
    assert name_similarity(from_column, to_table, to_column) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("integer", "integer", 1.0),
        ("integer", "bigint", 0.8),
        ("character varying(20)", "text", 0.8),
        ("character varying(20)", "character varying(20)", 1.0),
        ("integer", "text", 0.0),
        ("uuid", "uuid", 1.0),
        ("numeric(14,2)", "integer", 0.0),
        ("timestamp with time zone", "date", 0.0),
    ],
)
def test_type_match(a, b, expected):
    assert type_match(a, b) == expected


def test_score_adds_the_signals_and_stays_within_zero_and_one():
    full = {"name": 1.0, "type_": 1.0, "unique": 1.0}
    assert score(**full, overlap=None, joined=False) == pytest.approx(0.75)
    assert score(name=0.0, type_=1.0, unique=1.0, overlap=None, joined=False) == pytest.approx(0.35)
    assert score(name=0.0, type_=1.0, unique=1.0, overlap=None, joined=True) == pytest.approx(0.8)
    assert score(**full, overlap=1.0, joined=True) == 1.0
    assert score(**full, overlap=1.0, joined=False) == pytest.approx(1.0)
    # Once overlap is measured, names alone cannot carry a pair past the threshold.
    assert score(**full, overlap=0.0, joined=False) == pytest.approx(0.55)


def pairs(found):
    return {frozenset(p) for p in found}


def side(schema, table, column):
    return JoinSide(schema, table, column)


def test_joins_come_from_on_conditions_with_aliases_resolved():
    sql = """
    CREATE VIEW v AS
    SELECT c.cust_no FROM core.customers c JOIN core.accounts a ON a.cust_no = c.cust_no
    """
    assert pairs(routine_joins(sql, "postgres")) == {
        frozenset({side("core", "customers", "cust_no"), side("core", "accounts", "cust_no")})
    }


def test_joins_come_from_where_equalities_of_comma_joins_and_function_bodies():
    sql = """
    CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE core.accounts a SET balance = 0
        FROM core.customers c
        WHERE c.cust_no = a.cust_no AND a.acct_no = 5;
        RETURN (SELECT count(*) FROM core.transactions t, core.accounts a2
                WHERE t.acct_no = a2.acct_no);
    END $$
    """
    assert pairs(routine_joins(sql, "postgres")) == {
        frozenset({side("core", "customers", "cust_no"), side("core", "accounts", "cust_no")}),
        frozenset({side("core", "transactions", "acct_no"), side("core", "accounts", "acct_no")}),
    }


def test_a_tagged_dollar_quoted_body_ends_the_statement():
    sql = """CREATE FUNCTION f() RETURNS numeric LANGUAGE sql AS $function$
        SELECT 1 FROM core.transactions t JOIN core.accounts a ON a.acct_no = t.acct_no
    $function$"""
    assert len(routine_joins(sql, "postgres")) == 1


def test_tsql_procedure_text_is_scanned_statement_by_statement():
    sql = """
    CREATE PROCEDURE dbo.p @id int AS
    BEGIN
        SET NOCOUNT ON;
        SELECT o.id FROM dbo.Orders o INNER JOIN dbo.Customers c ON o.CustomerId = c.Id;
    END
    """
    assert pairs(routine_joins(sql, "tsql")) == {
        frozenset({side("dbo", "Orders", "CustomerId"), side("dbo", "Customers", "Id")})
    }


def test_filters_literals_unqualified_columns_and_self_joins_are_not_joins():
    sql = """
    SELECT 1 FROM core.accounts a JOIN core.customers c ON a.cust_no = c.cust_no
    WHERE a.balance = 0 AND c.full_name = 'x' AND iban = other
    """
    assert len(routine_joins(sql, "postgres")) == 1
    self_join = "SELECT 1 FROM t a JOIN t b ON a.id = b.id"
    assert routine_joins(self_join, "postgres") == []


def test_text_that_does_not_parse_yields_no_joins_instead_of_failing():
    assert routine_joins("this is ))) not sql JOIN", "postgres") == []
    assert routine_joins("", None) == []
