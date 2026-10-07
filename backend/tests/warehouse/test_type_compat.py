"""Data-type compatibility between a mapping's input and its target column (story 105)."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse.type_compat import check_types


def codes(source: dict, target: dict, *, source_null=False, target_null=True) -> list[str]:
    return [w.code for w in check_types(source, target, source_null, target_null)]


def dec(precision: int, scale: int) -> dict:
    return {"type": "decimal", "precision": precision, "scale": scale}


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ({"type": "integer"}, {"type": "bigint"}),
        ({"type": "string", "length": 50}, {"type": "string", "length": 100}),
        ({"type": "string", "length": 50}, {"type": "text"}),
        ({"type": "date"}, {"type": "timestamp"}),
        (dec(10, 2), dec(12, 2)),
        ({"type": "integer"}, {"type": "string", "length": 20}),
        ({"type": "uuid"}, {"type": "string", "length": 36}),
        ({"type": "smallint"}, dec(10, 2)),
    ],
)
def test_compatible_types_have_no_warnings(source, target):
    assert codes(source, target) == []


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ({"type": "bigint"}, {"type": "integer"}),
        ({"type": "string", "length": 200}, {"type": "string", "length": 100}),
        ({"type": "text"}, {"type": "string", "length": 100}),
        ({"type": "string"}, {"type": "char", "length": 10}),
        ({"type": "timestamp"}, {"type": "date"}),
        (dec(12, 4), dec(12, 2)),
        (dec(18, 2), dec(10, 2)),
        ({"type": "double"}, {"type": "float"}),
        (dec(10, 2), {"type": "integer"}),
        ({"type": "bigint"}, dec(10, 0)),
        ({"type": "uuid"}, {"type": "string", "length": 20}),
    ],
)
def test_narrowing_types_warn_about_truncation(source, target):
    assert codes(source, target) == ["may_truncate"]


def test_floating_point_into_exact_or_integer_may_lose_precision():
    assert codes({"type": "double"}, dec(10, 2)) == ["may_lose_precision"]
    assert codes({"type": "integer"}, {"type": "float"}) == ["may_lose_precision"]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ({"type": "string", "length": 10}, {"type": "integer"}),
        ({"type": "string"}, {"type": "date"}),
        ({"type": "boolean"}, {"type": "date"}),
        ({"type": "timestamp"}, {"type": "integer"}),
    ],
)
def test_unrelated_types_may_fail_to_convert(source, target):
    assert codes(source, target) == ["may_fail_conversion"]


def test_timestamp_with_and_without_zone_differ():
    assert codes({"type": "timestamp"}, {"type": "timestamptz"}) == ["may_fail_conversion"]


def test_a_nullable_input_into_a_required_column_warns():
    assert codes({"type": "string"}, {"type": "string"}, source_null=True, target_null=False) == [
        "nullable_into_required"
    ]
    assert codes({"type": "string"}, {"type": "string"}, source_null=False, target_null=False) == []
