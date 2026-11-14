"""The pure rules of relationship inference (spec §6.6): name similarity, type
compatibility, the rule score, JOIN conditions parsed from view and routine text, the union
of the rules' and the AI's pairs, the count query and the confidence from its counts."""

from __future__ import annotations

import uuid

import pytest
import sqlglot
from sqlglot import exp

from dawam.modules.sources.internal.inference import (
    AiGuess,
    Col,
    Counts,
    JoinSide,
    count_sql,
    counted_confidence,
    find_candidates,
    guess_score,
    name_similarity,
    reverse_wins,
    routine_joins,
    score,
    timed_out_confidence,
    type_match,
    union,
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


def test_the_rule_score_adds_the_signals_and_stays_within_zero_and_one():
    full = {"name": 1.0, "type_": 1.0, "unique": 1.0}
    assert score(**full, joined=False) == pytest.approx(0.75)
    assert score(name=0.0, type_=1.0, unique=1.0, joined=False) == pytest.approx(0.35)
    assert score(name=0.0, type_=1.0, unique=1.0, joined=True) == pytest.approx(0.8)
    assert score(**full, joined=True) == 1.0


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


def test_a_self_join_on_two_columns_of_one_table_is_a_join():
    sql = "SELECT 1 FROM s.employees e JOIN s.employees m ON e.manager_id = m.id"
    assert pairs(routine_joins(sql, "postgres")) == {
        frozenset({side("s", "employees", "manager_id"), side("s", "employees", "id")})
    }
    same_row = "SELECT 1 FROM s.t a JOIN s.u b ON a.id = b.id WHERE a.x = a.y"
    assert len(routine_joins(same_row, "postgres")) == 1, "a.x = a.y compares one row"


# -- AI + rules union, counts and the confidence (#608) -----------------------------------


def col(table, name, data_type="integer", unique=None, schema="s"):
    return Col(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{schema}.{table}.{name}"),
        uuid.uuid5(uuid.NAMESPACE_URL, f"{schema}.{table}"),
        schema,
        table,
        name,
        data_type,
        unique,
    )


def counts(*, matched=100, child=100, distinct=8000, unique=True):
    return Counts(
        target_rows=10_000,
        target_values=10_000,
        target_distinct=10_000 if unique else 9_000,
        child_values=child,
        child_distinct=distinct,
        matched_rows=matched,
    )


def test_the_guess_is_the_better_of_rules_and_ai_plus_a_bonus_when_both_found_it():
    assert guess_score(0.75, None, counted=True) == pytest.approx(0.75)
    assert guess_score(None, 0.9, counted=True) == pytest.approx(0.9)
    assert guess_score(0.75, 0.6, counted=True) == pytest.approx(0.80)
    assert guess_score(1.0, 0.9, counted=True) == 1.0, "at most 1"


def test_an_uncounted_ai_guess_is_capped_at_0_7():
    assert guess_score(None, 0.95, counted=False) == pytest.approx(0.7)
    assert guess_score(0.75, 0.95, counted=False) == pytest.approx(0.80)
    assert guess_score(None, 0.65, counted=False) == pytest.approx(0.65)


@pytest.mark.parametrize(
    ("guess", "found", "expected"),
    [
        (0.75, counts(matched=100, distinct=8000), 0.93),
        (0.60, counts(matched=100, distinct=40), 0.88),
        (0.75, counts(matched=85, distinct=40), 0.82),
        (0.60, counts(matched=100, distinct=3), 0.53),
        (0.75, counts(matched=40, distinct=40), 0.51),
    ],
)
def test_a_counted_pair_reproduces_the_examples(guess, found, expected):
    # The examples are shown to two places, rounded half up.
    assert round(counted_confidence(guess, found) + 1e-9, 2) == expected


def test_a_target_that_is_not_unique_scores_0_3_whatever_the_guess():
    assert counted_confidence(1.0, counts(unique=False)) == 0.3


def test_fewer_than_five_distinct_child_values_halve_the_match_rate():
    assert counted_confidence(0.0, counts(distinct=4)) == pytest.approx(0.35)
    assert counted_confidence(0.0, counts(distinct=5)) == pytest.approx(0.7)


@pytest.mark.parametrize(("guess", "expected"), [(1.0, 0.85), (0.75, 0.60), (0.60, 0.60)])
def test_a_timed_out_count_lowers_the_guess_but_keeps_it_visible(guess, expected):
    assert timed_out_confidence(guess) == pytest.approx(expected)


def test_counts_give_the_match_rate_and_the_orphans():
    found = Counts(10, 10, 10, child_values=8, child_distinct=6, matched_rows=6)
    assert found.target_unique and found.match_rate == 0.75 and found.orphan_rows == 2
    assert Counts(10, 10, 10, 0, 0, 0).match_rate == 0.0
    assert not Counts(0, 0, 0, 0, 0, 0).target_unique, "an empty target proves nothing"


def test_the_direction_with_the_unique_target_wins_else_the_higher_match_rate():
    assert not reverse_wins(counts(unique=True), counts(unique=True))
    assert reverse_wins(counts(unique=False), counts(unique=True))
    assert not reverse_wins(counts(unique=True), counts(unique=False))
    assert reverse_wins(counts(unique=False, matched=50), counts(unique=False, matched=90))


def test_ai_and_rule_pairs_are_unioned_in_either_direction():
    customers_id = col("customers", "id", unique="primary key")
    orders_customer = col("orders", "customer_id")
    orders_ref = col("orders", "acct_ref", "text")
    accounts_no = col("accounts", "account_no", "character varying(20)")
    rules = find_candidates(
        [customers_id, orders_customer, orders_ref, accounts_no], {}, set(), threshold=0.6
    )
    assert [(c.from_col, c.to_col) for c in rules] == [(orders_customer, customers_id)]

    merged = union(
        rules,
        [
            # The same pair, the other way round: one pair, found by both.
            AiGuess(customers_id, orders_customer, 0.8, "customer key"),
            AiGuess(orders_ref, accounts_no, 0.7, "acct_ref holds account numbers"),
        ],
        declared=set(),
        excluded=set(),
    )

    by_pair = {(c.from_col.name, c.to_col.name): c for c in merged}
    assert set(by_pair) == {("customer_id", "id"), ("acct_ref", "account_no")}
    both = by_pair["customer_id", "id"]
    assert both.found_by == "both" and both.origin == "inferred"
    assert both.ai is not None and both.ai.reason == "customer key"
    ai_only = by_pair["acct_ref", "account_no"]
    assert ai_only.found_by == "ai" and ai_only.origin == "ai"
    assert ai_only.rule_score is None


def test_the_union_drops_declared_excluded_and_type_incompatible_pairs():
    a, b = col("a", "b_id"), col("b", "id", unique="primary key")
    c, d = col("c", "x"), col("d", "y", "date")
    e, f = col("e", "ref"), col("f", "code")
    merged = union(
        [],
        [AiGuess(a, b, 0.9, ""), AiGuess(c, d, 0.9, ""), AiGuess(e, f, 0.9, "")],
        declared={frozenset((a.id, b.id))},
        excluded={frozenset((f.id, e.id))},
    )
    assert merged == []


def test_a_self_reference_is_proposed_by_the_rules_and_by_the_ai():
    emp_id = col("employees", "id", unique="primary key")
    manager = col("employees", "manager_id")
    boss = col("employees", "boss_id")
    joined = {(manager.id, emp_id.id): ["view s.org_chart"]}

    rules = find_candidates([emp_id, manager, boss], joined, set(), threshold=0.6)
    merged = union(rules, [AiGuess(boss, emp_id, 0.8, "the boss")], declared=set(), excluded=set())

    pairs_found = {(c.from_col.name, c.to_col.name): c.found_by for c in merged}
    assert pairs_found == {("manager_id", "id"): "rules", ("boss_id", "id"): "ai"}


def test_the_candidate_confidence_follows_how_it_was_counted():
    customers_id = col("customers", "id", unique="primary key")
    orders_customer = col("orders", "customer_id")
    [candidate] = find_candidates([customers_id, orders_customer], {}, set(), threshold=0.6)
    assert candidate.rule_score == pytest.approx(0.75)

    assert candidate.confidence == pytest.approx(0.75), "not counted: the guess"
    assert candidate.evidence(0.6)["counts"]["status"] == "no_connection"
    candidate.count_status = "timed_out"
    assert candidate.confidence == pytest.approx(0.60)
    assert candidate.evidence(0.6)["counts"] == {"status": "timed_out", "note": "count timed out"}
    candidate.count_status, candidate.counts = "counted", counts(distinct=8000)
    assert candidate.confidence == pytest.approx(0.925)
    evidence = candidate.evidence(0.6)
    assert evidence["found_by"] == "rules"
    assert evidence["counts"]["orphan_rows"] == 0 and evidence["counts"]["match_rate"] == 1.0
    candidate.count_status, candidate.counts, candidate.count_reason = "failed", None, "boom"
    assert candidate.confidence == pytest.approx(0.75)
    assert candidate.evidence(0.6)["counts"] == {"status": "failed", "note": "boom"}


def test_an_ai_only_pair_keeps_its_own_confidence_once_counted():
    accounts_no = col("accounts", "account_no", "text")
    acct_ref = col("orders", "acct_ref", "text")
    [candidate] = union(
        [], [AiGuess(acct_ref, accounts_no, 0.95, "x")], declared=set(), excluded=set()
    )
    assert candidate.confidence == pytest.approx(0.7), "uncounted AI guess capped"
    candidate.count_status, candidate.counts = "counted", counts(distinct=40)
    assert candidate.confidence == pytest.approx(0.7 + 0.3 * 0.95)
    assert candidate.evidence(0.6)["ai_guess"] == {"confidence": 0.95, "reason": "x"}


@pytest.mark.parametrize("dialect", ["postgres", "tsql", "mysql", "oracle"])
def test_the_count_query_only_joins_and_counts_the_two_columns(dialect):
    child = col("orders", "national_id", "text", schema="core")
    parent = col("people", "national_id", "text", unique="primary key", schema="core")

    sql = count_sql(child, parent, dialect)

    tree = sqlglot.parse_one(sql, read=dialect)
    assert isinstance(tree, exp.Select)
    assert [p.alias_or_name.lower() for p in tree.expressions] == [
        "target_rows",
        "target_values",
        "target_distinct",
        "child_values",
        "child_distinct",
        "matched_rows",
    ]
    touched = [c for c in tree.find_all(exp.Column) if c.name == "national_id"]
    assert touched
    for column in touched:
        assert column.find_ancestor(exp.Count) or column.find_ancestor(exp.Join), column.sql()
    assert not list(tree.find_all(exp.Literal)), "no value is compared"
    for inner in tree.find_all(exp.Select):
        if inner is not tree:
            for projection in inner.expressions:
                assert isinstance(projection.unalias(), exp.Count), projection.sql()


def test_the_count_query_quotes_names_and_counts_a_self_reference():
    emp_id = col("employees", "id", unique="primary key", schema="hr")
    manager = col("employees", "manager_id", schema="hr")
    sql = count_sql(manager, emp_id, "postgres")
    assert sql.count('"hr"."employees"') == 3
