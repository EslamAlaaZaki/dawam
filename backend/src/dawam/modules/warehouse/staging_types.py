"""Source type to staging type (spec §6.7, story 84).

Pure: a source column's data type text (as the Snapshot stores it, for any supported
engine), the source engine when known, and the target platform in; a neutral type
(the form ``dw_columns.data_type`` stores, which the DDL export renders per platform) and
review flags out. The table is per platform where platforms differ (the longest string a
platform can type, and the default for a ``NUMBER`` without precision) and has explicit
fallbacks: a type no rule knows becomes text and is flagged, and a translation that can
lose something (precision, a time zone, a range) is flagged as lossy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .platforms import TargetPlatform
from .staging_naming import Flag

MAX_DECIMAL_PRECISION = 38


@dataclass(frozen=True)
class PlatformTypes:
    max_string_length: int
    """The longest ``varchar`` the platform types; a longer one becomes ``text``."""
    default_decimal: tuple[int, int]
    """``NUMERIC`` with no precision (Oracle ``NUMBER``): the widest the platform keeps."""
    max_scale: int


PLATFORM_TYPES: dict[TargetPlatform, PlatformTypes] = {
    "postgresql": PlatformTypes(1_000_000, (38, 10), 38),
    "sqlserver": PlatformTypes(8000, (38, 10), 38),
    "oracle": PlatformTypes(4000, (38, 10), 38),
    "snowflake": PlatformTypes(1_000_000, (38, 10), 37),
    "bigquery": PlatformTypes(1_000_000, (38, 9), 9),
}


@dataclass(frozen=True)
class TranslatedType:
    data_type: dict[str, Any]
    flags: tuple[Flag, ...] = ()


_PARAMS = re.compile(r"\(([^)]*)\)")
_UNSIGNED = re.compile(r"\b(unsigned|zerofill|signed)\b")

_INTEGER = {"smallint", "int2", "smallserial", "tinyint", "int", "integer", "int4", "mediumint"}
_INTEGER |= {"serial", "serial4", "year"}
_BIGINT = {"bigint", "int8", "bigserial", "serial8"}
_TEXT = {
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "ntext",
    "clob",
    "nclob",
    "long",
    "citext",
    "xml",
    "xmltype",
    "sql_variant",
}
_BINARY = {"bytea", "blob", "tinyblob", "mediumblob", "longblob", "image", "long raw", "raw"}
_BINARY |= {"binary", "varbinary", "bfile"}
_VARCHAR = {
    "varchar",
    "character varying",
    "nvarchar",
    "varchar2",
    "nvarchar2",
    "varying character",
    "national character varying",
    "nchar varying",
    "name",
}
_CHAR = {"char", "character", "bpchar", "nchar", "national character"}
_TIMESTAMP = {
    "timestamp",
    "timestamp without time zone",
    "datetime",
    "datetime2",
    "smalldatetime",
    "timestamp with local time zone",
}
_TIMESTAMPTZ = {"timestamptz", "timestamp with time zone", "datetimeoffset"}
_STRING_FALLBACKS = {
    "interval": 64,
    "hierarchyid": 4000,
    "inet": 45,
    "cidr": 49,
    "macaddr": 17,
    "oid": 20,
    "urowid": 4000,
    "rowid": 18,
    "enum": 255,
    "set": 255,
}
_GEOMETRY = {"geometry", "geography", "point", "line", "polygon", "sdo_geometry"}


def _flag(code: str, message: str) -> Flag:
    return Flag(code, message)  # type: ignore[arg-type]


def _lossy(message: str) -> Flag:
    return _flag("lossy_type", message)


def _fallback(message: str) -> Flag:
    return _flag("fallback_type", message)


def _neutral(kind: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "length": None, "precision": None, "scale": None} | extra


def _ints(text: str) -> list[int | str]:
    parts = [p.strip().lower() for p in text.split(",") if p.strip()]
    return [int(p) if re.fullmatch(r"-?\d+", p) else p for p in parts]


def translate_type(
    platform: TargetPlatform, source_type: str, *, engine: str | None = None
) -> TranslatedType:
    """The staging type of ``source_type``; ``engine`` (``oracle``, ``sqlserver``, ...) settles
    the few names that mean different things per engine (Oracle ``DATE`` holds a time,
    SQL Server ``timestamp`` is a row version)."""
    limits = PLATFORM_TYPES[platform]
    text = source_type.strip().lower()
    if not text:
        return TranslatedType(_neutral("text"), (_fallback("The source type is empty."),))
    if text.endswith("[]") or text.startswith("_") or text.startswith("array"):
        return TranslatedType(
            _neutral("json"), (_fallback(f"{source_type} (an array) is kept as JSON."),)
        )
    params: list[int | str] = []
    match = _PARAMS.search(text)
    if match:
        params = _ints(match.group(1))
        text = _PARAMS.sub("", text, count=1)
    text = " ".join(_UNSIGNED.sub("", text).split())

    if engine == "sqlserver" and text in ("timestamp", "rowversion"):
        return TranslatedType(_neutral("binary", length=8))
    if engine == "oracle" and text == "date":
        return TranslatedType(_neutral("timestamp"))
    if text in _INTEGER:
        small = text in ("smallint", "int2", "smallserial", "tinyint")
        return TranslatedType(_neutral("smallint" if small else "integer"))
    if text in _BIGINT:
        return TranslatedType(_neutral("bigint"))
    if text in ("numeric", "decimal", "number", "dec", "money", "smallmoney"):
        return _decimal(limits, source_type, text, params)
    if text in ("float", "double precision", "double", "float8", "binary_double", "float64"):
        if text == "float" and params and isinstance(params[0], int) and params[0] <= 24:
            return TranslatedType(_neutral("float"))
        return TranslatedType(_neutral("double"))
    if text in ("real", "float4", "binary_float"):
        return TranslatedType(_neutral("float"))
    if text in ("boolean", "bool", "bit"):
        return TranslatedType(_neutral("boolean"))
    if text == "date":
        return TranslatedType(_neutral("date"))
    if text in ("time", "time without time zone"):
        return TranslatedType(_neutral("time"))
    if text in ("timetz", "time with time zone"):
        return TranslatedType(
            _neutral("time"), (_lossy(f"{source_type} loses its time zone as a time."),)
        )
    if text in _TIMESTAMP or re.fullmatch(r"timestamp( with local time zone)?", text):
        return TranslatedType(_neutral("timestamp"))
    if text in _TIMESTAMPTZ:
        return TranslatedType(_neutral("timestamptz"))
    if text in ("uuid", "uniqueidentifier"):
        return TranslatedType(_neutral("uuid"))
    if text in ("json", "jsonb"):
        return TranslatedType(_neutral("json"))
    if text in _CHAR or text in _VARCHAR:
        return _character(limits, text in _CHAR, source_type, params)
    if text in _TEXT:
        flags = ()
        if text in ("xml", "xmltype", "sql_variant"):
            flags = (_fallback(f"{source_type} is kept as text."),)
        return TranslatedType(_neutral("text"), flags)
    if text in _BINARY:
        length = params[0] if params and isinstance(params[0], int) else None
        if length is not None and not 1 <= length <= 1_000_000:
            length = None
        return TranslatedType(_neutral("binary", length=length))
    if text in _GEOMETRY:
        return TranslatedType(
            _neutral("text"), (_fallback(f"{source_type} (a spatial type) is kept as text."),)
        )
    for prefix, length in _STRING_FALLBACKS.items():
        if text == prefix or text.startswith(prefix + " "):
            return TranslatedType(
                _neutral("string", length=length),
                (_lossy(f"{source_type} is kept as text of up to {length} characters."),),
            )
    return TranslatedType(
        _neutral("text"), (_fallback(f"No rule for {source_type!r}: kept as text."),)
    )


def _decimal(
    limits: PlatformTypes, source_type: str, text: str, params: list[int | str]
) -> TranslatedType:
    if text == "money":
        return TranslatedType(_neutral("decimal", precision=19, scale=min(4, limits.max_scale)))
    if text == "smallmoney":
        return TranslatedType(_neutral("decimal", precision=10, scale=min(4, limits.max_scale)))
    numbers = [p for p in params if isinstance(p, int)]
    if not numbers:
        precision, scale = limits.default_decimal
        return TranslatedType(
            _neutral("decimal", precision=precision, scale=scale),
            (
                _lossy(
                    f"{source_type} has no precision: kept as DECIMAL({precision},{scale}), "
                    "which can lose digits."
                ),
            ),
        )
    precision = numbers[0]
    scale = numbers[1] if len(numbers) > 1 else 0
    flags: list[Flag] = []
    if precision > MAX_DECIMAL_PRECISION:
        flags.append(_lossy(f"{source_type} is wider than {MAX_DECIMAL_PRECISION} digits."))
        precision = MAX_DECIMAL_PRECISION
    if scale < 0:  # Oracle allows a negative scale (rounding left of the point)
        flags.append(_lossy(f"{source_type} has a negative scale: kept with scale 0."))
        scale = 0
    if scale > limits.max_scale:
        flags.append(
            _lossy(f"The scale of {source_type} is over the platform's {limits.max_scale}.")
        )
        scale = limits.max_scale
    scale = min(scale, precision)
    return TranslatedType(_neutral("decimal", precision=precision, scale=scale), tuple(flags))


def _character(
    limits: PlatformTypes, fixed: bool, source_type: str, params: list[int | str]
) -> TranslatedType:
    length = params[0] if params and isinstance(params[0], int) else None
    if params and params[0] == "max":
        return TranslatedType(_neutral("text"))
    if fixed:
        return TranslatedType(_neutral("char", length=length or 1))
    if length is not None and length > limits.max_string_length:
        return TranslatedType(
            _neutral("text"),
            (_fallback(f"{source_type} is longer than the platform's varchar: kept as text."),),
        )
    return TranslatedType(_neutral("string", length=length))
