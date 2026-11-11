"""The Profiling page's rules (spec §6.5, story 57a): findings per column and data growth
per table. Both are pure, so they are checked directly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dawam.modules.sources.internal.profiling_overview import (
    ColumnFacts,
    Point,
    finding_of,
    growth_of,
)

START = datetime(2026, 8, 14, tzinfo=UTC)


def facts(**changes) -> ColumnFacts:
    values = {
        "row_count": 100,
        "null_pct": 0.0,
        "distinct_count": 40,
        "row_cap": 100_000,
        "min": "a",
        "is_protected": False,
        "is_key": False,
    }
    return ColumnFacts(**{**values, **changes})


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"null_pct": 100.0, "distinct_count": 0}, "all_null"),
        ({"null_pct": 50.1}, "mostly_null"),
        ({"null_pct": 50.0}, None),
        ({"distinct_count": 1}, "constant"),
        ({"distinct_count": 100}, "unique_not_key"),
        ({"distinct_count": 100, "is_key": True}, None),
        ({"distinct_count": None}, None),
        ({}, None),
    ],
)
def test_each_rule_finds_its_kind(changes, kind):
    found = finding_of(facts(**changes))
    assert (found.kind if found else None) == kind


def test_the_first_matching_rule_wins():
    # Mostly null and constant at once: the null rule comes first.
    found = finding_of(facts(null_pct=80.0, distinct_count=1))
    assert found is not None and found.kind == "mostly_null"


def test_an_empty_sample_has_no_constant_or_unique_finding():
    assert finding_of(facts(row_count=0, distinct_count=0)) is None


def test_a_constant_shows_its_value():
    found = finding_of(facts(distinct_count=1, min="WEB"))
    assert found is not None and found.value == "WEB"


def test_a_unique_column_of_a_capped_sample_says_so():
    found = finding_of(facts(row_count=100, row_cap=100, distinct_count=100))
    assert found is not None and found.kind == "unique_not_key" and found.capped is True
    found = finding_of(facts(distinct_count=100))
    assert found is not None and found.capped is False


def test_a_protected_column_gets_only_the_null_rules():
    assert finding_of(facts(is_protected=True, distinct_count=1, min="X")) is None
    assert finding_of(facts(is_protected=True, distinct_count=100)) is None
    found = finding_of(facts(is_protected=True, null_pct=100.0))
    assert found is not None and found.kind == "all_null" and found.value is None


def series(*estimates: int, every_days: int = 10) -> list[Point]:
    return [
        Point(taken_at=START + timedelta(days=i * every_days), row_estimate=e)
        for i, e in enumerate(estimates)
    ]


def test_growth_is_linear_from_the_first_to_the_latest_snapshot():
    growth = growth_of(series(1000, 1100, 1300, every_days=30))

    assert growth is not None
    assert (growth.first, growth.latest) == (1000, 1300)
    assert growth.change_pct == pytest.approx(30.0)
    assert growth.per_month == pytest.approx(300 / 60 * 30.44)
    assert growth.in_12_months == pytest.approx(1300 + growth.per_month * 12)
    assert growth.dropped_at is None


def test_a_drop_of_more_than_a_fifth_resets_the_rate():
    points = series(30_000, 34_000, 12_000, 18_000)

    growth = growth_of(points)

    assert growth is not None and growth.dropped_at == points[2].taken_at
    assert growth.per_month == pytest.approx(6_000 / 10 * 30.44)
    assert growth.change_pct == pytest.approx((18_000 - 30_000) / 30_000 * 100)


def test_a_drop_of_a_fifth_or_less_is_not_a_reset():
    growth = growth_of(series(1000, 800, 900))
    assert growth is not None and growth.dropped_at is None


def test_no_change_or_too_few_points_has_no_growth():
    assert growth_of(series(500, 500, 500)) is None
    assert growth_of(series(500)) is None
    assert growth_of([]) is None


def test_growth_from_zero_has_no_change_percentage():
    growth = growth_of(series(0, 100))
    assert growth is not None and growth.change_pct is None


def test_a_projection_never_goes_below_zero():
    growth = growth_of(series(1000, 850, 700, 560, every_days=30))
    assert growth is not None and growth.per_month < 0 and growth.in_12_months == 0
