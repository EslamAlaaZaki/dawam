"""Data-type compatibility of a mapping's input and its target column (spec §6.14, story 105).

Pure: the neutral types of ``dw_columns.data_type`` (``{"type", "length", "precision",
"scale"}``) are compared and each way the load may lose data or fail is a warning. Warnings
never block a save; they are advice for the Editor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_INTEGER_DIGITS = {"smallint": 5, "integer": 10, "bigint": 19}
_INTEGER_RANK = {"smallint": 1, "integer": 2, "bigint": 3}
_TEXT = ("char", "string", "text")
_APPROX = ("float", "double")
_TEXT_WIDTH = {
    "smallint": 6,
    "integer": 11,
    "bigint": 20,
    "float": 24,
    "double": 24,
    "boolean": 5,
    "date": 10,
    "time": 18,
    "timestamp": 32,
    "timestamptz": 35,
    "uuid": 36,
}
"""The most characters a value of the type takes as text."""


@dataclass(frozen=True)
class TypeWarning:
    code: str
    """``may_truncate``, ``may_lose_precision``, ``may_fail_conversion`` or
    ``nullable_into_required``."""
    message: str


def _label(t: dict[str, Any]) -> str:
    kind = t.get("type", "?")
    if t.get("length"):
        return f"{kind}({t['length']})"
    if t.get("precision") is not None:
        return f"{kind}({t['precision']},{t.get('scale') or 0})"
    return str(kind)


def _truncate(source: dict[str, Any], target: dict[str, Any], why: str) -> TypeWarning:
    return TypeWarning(
        "may_truncate", f"{_label(source)} into {_label(target)} may be truncated: {why}."
    )


def _fails(source: dict[str, Any], target: dict[str, Any]) -> TypeWarning:
    return TypeWarning(
        "may_fail_conversion",
        f"{_label(source)} does not convert to {_label(target)} for every value.",
    )


def _precision(source: dict[str, Any], target: dict[str, Any], what: str) -> TypeWarning:
    return TypeWarning(
        "may_lose_precision", f"{_label(source)} into {_label(target)} may lose {what}."
    )


def check_types(
    source: dict[str, Any],
    target: dict[str, Any],
    source_nullable: bool,
    target_nullable: bool,
) -> list[TypeWarning]:
    """Every way an input of type ``source`` may not fit a column of type ``target``."""
    warnings: list[TypeWarning] = []
    found = _type_warning(source, target)
    if found is not None:
        warnings.append(found)
    if source_nullable and not target_nullable:
        warnings.append(
            TypeWarning(
                "nullable_into_required",
                "The input can be NULL but the target column does not allow NULL.",
            )
        )
    return warnings


def _type_warning(source: dict[str, Any], target: dict[str, Any]) -> TypeWarning | None:
    s, t = source.get("type"), target.get("type")
    if s is None or t is None:
        return None
    if s in _TEXT and t in _TEXT:
        return _text_to_text(source, target)
    if t in _TEXT:
        width, limit = _TEXT_WIDTH.get(s), target.get("length")
        if s == "decimal" and source.get("precision"):
            width = source["precision"] + 2  # digits, sign and decimal point
        if width and limit and limit < width:
            return _truncate(source, target, f"the text form takes up to {width} characters")
        return None
    if s in _INTEGER_RANK:
        return _from_integer(source, target)
    if s == "decimal":
        return _from_decimal(source, target)
    if s == t:
        return None
    if s in _APPROX:
        if s == "double" and t == "float":
            return _truncate(source, target, "double precision into single")
        if t in _APPROX:
            return None
        if t in _INTEGER_RANK or t == "decimal":
            return _precision(source, target, "its fraction or digits")
    if s == "date" and t in ("timestamp", "timestamptz"):
        return None
    if s == "timestamp" and t == "timestamptz":
        return None
    if s == "timestamptz" and t == "timestamp":
        return _truncate(source, target, "the time zone is dropped")
    if s in ("timestamp", "timestamptz") and t == "date":
        return _truncate(source, target, "the time of day is dropped")
    return _fails(source, target)


def _text_to_text(source: dict[str, Any], target: dict[str, Any]) -> TypeWarning | None:
    limit = target.get("length")
    if not limit:
        return None
    length = source.get("length")
    if source["type"] == "text" or not length:
        return _truncate(source, target, "the input has no length limit")
    if length > limit:
        return _truncate(source, target, f"{length} characters do not fit in {limit}")
    return None


def _from_integer(source: dict[str, Any], target: dict[str, Any]) -> TypeWarning | None:
    s, t = source["type"], target["type"]
    if t in _INTEGER_RANK:
        if _INTEGER_RANK[s] > _INTEGER_RANK[t]:
            return _truncate(source, target, "the value may overflow")
        return None
    if t == "decimal":
        precision = target.get("precision")
        if precision is not None and precision - (target.get("scale") or 0) < _INTEGER_DIGITS[s]:
            return _truncate(source, target, "the value may overflow")
        return None
    if t == "float":
        if s == "smallint":
            return None  # exact in single precision
        return _precision(source, target, "precision for large values")
    if t == "double":
        return _precision(source, target, "precision for large values") if s == "bigint" else None
    return _fails(source, target)


def _from_decimal(source: dict[str, Any], target: dict[str, Any]) -> TypeWarning | None:
    t = target["type"]
    scale = source.get("scale") or 0
    if t in _INTEGER_RANK:
        if scale == 0 and (source.get("precision") or 99) <= _INTEGER_DIGITS[t]:
            return None
        return _truncate(source, target, "the fraction is dropped or the value may overflow")
    if t == "decimal":
        p_t, s_t = target.get("precision"), target.get("scale") or 0
        if p_t is None:
            return None
        p_s = source.get("precision")
        if s_t < scale:
            return _truncate(source, target, f"scale {scale} does not fit scale {s_t}")
        if p_s is None or p_s - scale > p_t - s_t:
            return _truncate(source, target, "the integer digits may not fit")
        return None
    if t in _APPROX:
        return None
    return _fails(source, target)
