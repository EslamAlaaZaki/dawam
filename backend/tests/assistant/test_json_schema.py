"""The small JSON Schema checker that guards prompted tool calls (the schemas are the
tools' own pydantic output, so only that subset is supported)."""

from __future__ import annotations

from dawam.modules.assistant.internal.json_schema import validate

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "kind": {"enum": ["kpi", "view"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "note": {"anyOf": [{"type": "string", "maxLength": 5}, {"type": "null"}]},
        "ref": {"$ref": "#/$defs/Ref"},
    },
    "required": ["id"],
    "additionalProperties": False,
    "$defs": {"Ref": {"type": "object", "properties": {"n": {"type": "number"}}}},
}
UUID = "6f9619ff-8b86-d011-b42d-00c04fc964ff"


def test_a_valid_value_has_no_errors():
    value = {"id": UUID, "limit": 3, "kind": "kpi", "tags": ["a"], "note": None, "ref": {"n": 1.5}}
    assert validate(value, SCHEMA) == []


def test_missing_required_and_unknown_properties_are_reported():
    errors = validate({"extra": 1}, SCHEMA)
    assert any("id" in e and "required" in e for e in errors)
    assert any("not allowed" in e for e in errors)


def test_types_are_strict_and_a_bool_is_not_a_number():
    assert validate({"id": UUID, "limit": "3"}, SCHEMA)
    assert validate({"id": UUID, "limit": True}, SCHEMA)
    assert validate({"id": UUID, "limit": 3.5}, SCHEMA)
    assert validate({"id": 5}, SCHEMA)


def test_bounds_enums_formats_and_nested_items():
    assert validate({"id": UUID, "limit": 0}, SCHEMA)
    assert validate({"id": UUID, "limit": 51}, SCHEMA)
    assert validate({"id": UUID, "kind": "other"}, SCHEMA)
    assert validate({"id": "not-a-uuid"}, SCHEMA)
    assert validate({"id": UUID, "tags": ["a", "b", "c"]}, SCHEMA)
    assert validate({"id": UUID, "tags": [1]}, SCHEMA)
    assert validate({"id": UUID, "ref": {"n": "x"}}, SCHEMA)


def test_any_of_accepts_one_matching_branch_only():
    assert validate({"id": UUID, "note": "abc"}, SCHEMA) == []
    assert validate({"id": UUID, "note": "toolongvalue"}, SCHEMA)
    assert validate({"id": UUID, "note": 4}, SCHEMA)


def test_errors_name_the_place_but_never_echo_the_value():
    errors = validate({"id": UUID, "kind": "SECRET-VALUE"}, SCHEMA)
    assert errors and all("SECRET-VALUE" not in e for e in errors)
    assert any("kind" in e for e in errors)


def test_an_empty_schema_accepts_anything():
    assert validate({"a": [1]}, {}) == []
    assert validate({}, {"type": "object"}) == []
