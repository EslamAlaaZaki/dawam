"""A small JSON Schema checker for prompted tool calls (spec §6.18, limited mode).

It supports the subset the tools' own (pydantic-generated) schemas use: ``type``,
``properties``, ``required``, ``additionalProperties``, ``items``, ``enum``, ``const``,
``anyOf``, ``$ref`` into ``$defs``, string/number/array bounds and the ``uuid`` format.
Error messages name the place and the rule, never the value: the value came from an
untrusted model and the messages go back to it.
"""

from __future__ import annotations

import uuid
from typing import Any

MAX_DEPTH = 32


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """The reasons ``value`` does not satisfy ``schema`` (empty when it does)."""
    errors: list[str] = []
    _check(value, schema, schema, "arguments", errors, 0)
    return errors


def _type_ok(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def _check(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
    depth: int,
) -> None:
    if depth > MAX_DEPTH:
        errors.append(f"{path}: nested too deeply")
        return
    if "$ref" in schema:
        target = _resolve(str(schema["$ref"]), root)
        if target is None:
            errors.append(f"{path}: the schema has an unresolvable reference")
        else:
            _check(value, target, root, path, errors, depth + 1)
        return
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            trial: list[str] = []
            _check(value, branch, root, path, trial, depth + 1)
            if not trial:
                break
        else:
            errors.append(f"{path}: does not match any allowed form")
            return
    wanted = schema.get("type")
    if wanted is not None:
        names = wanted if isinstance(wanted, list) else [wanted]
        if not any(_type_ok(value, n) for n in names):
            errors.append(f"{path}: must be of type {'/'.join(names)}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: is not the allowed constant")
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        errors.append(f"{path}: must be one of {allowed}")
    if isinstance(value, str):
        _check_string(value, schema, path, errors)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _check_number(value, schema, path, errors)
    elif isinstance(value, list):
        _check_array(value, schema, root, path, errors, depth)
    elif isinstance(value, dict):
        _check_object(value, schema, root, path, errors, depth)


def _check_string(value: str, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: is too short")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{path}: is too long")
    if schema.get("format") == "uuid":
        try:
            uuid.UUID(value)
        except ValueError:
            errors.append(f"{path}: must be a UUID")


def _check_number(value: float, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: is below the minimum {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(f"{path}: is above the maximum {schema['maximum']}")


def _check_array(
    value: list[Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
    depth: int,
) -> None:
    if len(value) < schema.get("minItems", 0):
        errors.append(f"{path}: has too few items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path}: has too many items")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _check(item, items, root, f"{path}[{index}]", errors, depth + 1)


def _check_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
    depth: int,
) -> None:
    properties: dict[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{path}.{name}: is required")
    for name, item in value.items():
        if name in properties:
            _check(item, properties[name], root, f"{path}.{name}", errors, depth + 1)
        elif schema.get("additionalProperties") is False:
            errors.append(f"{path}: the property {_label(name)} is not allowed")
        elif isinstance(schema.get("additionalProperties"), dict):
            _check(
                item,
                schema["additionalProperties"],
                root,
                f"{path}.{_label(name)}",
                errors,
                depth + 1,
            )


def _label(name: str) -> str:
    """A property name the model chose, safe to put back in a message."""
    return name if name.isidentifier() and len(name) <= 40 else "(unusual name)"
