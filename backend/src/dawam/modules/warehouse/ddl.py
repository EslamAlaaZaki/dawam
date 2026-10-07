"""DDL for the DW Schema in a target platform's dialect (spec §6.9, story 97).

Pure: plain tables in, one SQL script out; nothing here reads the database. Column types
are stored neutral (ANSI-like type plus length, precision and scale) and are translated to
the platform only here. A package holds, in this order: the Layers' physical schemas, the
tables, the foreign keys (``ALTER TABLE``, so table order never matters), and one
unknown-member ``INSERT`` per dimension (spec: DAWAM loads no data in Release 1).

A foreign key is emitted only when the referenced table is in the same package, so a
single-Layer package never refers to a table it does not create. Names that are not safe
unquoted on the platform (a reserved word, an odd character) are quoted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .platforms import (
    SAFE_IDENTIFIER,
    TargetPlatform,
    is_reserved_word,
    max_identifier_length,
)

LAYER_ORDER = ("staging", "core", "mart")
UNKNOWN_MEMBER_KEY = -1


@dataclass(frozen=True)
class DdlColumn:
    name: str
    data_type: Mapping[str, Any]
    is_nullable: bool
    role: str
    references_table_id: Any = None


@dataclass(frozen=True)
class DdlTable:
    id: Any
    layer: str
    name: str
    kind: str
    columns: Sequence[DdlColumn] = field(default_factory=tuple)
    unknown_member: Mapping[str, Any] | None = None
    """``{"surrogate_key": -1, "defaults": {column: value}}`` for a dimension."""


# --- identifiers ----------------------------------------------------------------------

_QUOTES = {
    "postgresql": ('"', '"'),
    "oracle": ('"', '"'),
    "snowflake": ('"', '"'),
    "sqlserver": ("[", "]"),
    "bigquery": ("`", "`"),
}


def ident(platform: TargetPlatform, name: str) -> str:
    """``name`` as an identifier: bare when safe on every platform, quoted otherwise."""
    if SAFE_IDENTIFIER.fullmatch(name) and not is_reserved_word(platform, name):
        return name
    opening, closing = _QUOTES[platform]
    return opening + name.replace(closing, closing * 2) + closing


def _constraint_name(platform: TargetPlatform, prefix: str, table: str, column: str = "") -> str:
    name = "_".join(p for p in (prefix, table, column) if p)
    if len(name) <= max_identifier_length(platform):
        return name
    digest = hashlib.sha1(name.encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


# --- types ----------------------------------------------------------------------------


def _sized(base: str, length: int | None, fallback: str | None = None) -> str:
    if length is None:
        return fallback if fallback is not None else base
    return f"{base}({length})"


def _decimal(base: str, t: Mapping[str, Any], default: str | None = None) -> str:
    precision, scale = t.get("precision"), t.get("scale")
    if precision is None:
        return default or base
    return f"{base}({precision},{scale or 0})"


def _postgresql(t: Mapping[str, Any]) -> str:
    return {
        "smallint": "SMALLINT",
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "decimal": _decimal("NUMERIC", t),
        "float": "REAL",
        "double": "DOUBLE PRECISION",
        "boolean": "BOOLEAN",
        "char": _sized("CHAR", t.get("length")),
        "string": _sized("VARCHAR", t.get("length")),
        "text": "TEXT",
        "binary": "BYTEA",
        "date": "DATE",
        "time": "TIME",
        "timestamp": "TIMESTAMP",
        "timestamptz": "TIMESTAMPTZ",
        "uuid": "UUID",
        "json": "JSONB",
    }[t["type"]]


def _sqlserver(t: Mapping[str, Any]) -> str:
    length = t.get("length")
    return {
        "smallint": "SMALLINT",
        "integer": "INT",
        "bigint": "BIGINT",
        "decimal": _decimal("DECIMAL", t, "DECIMAL(38,10)"),
        "float": "REAL",
        "double": "FLOAT(53)",
        "boolean": "BIT",
        "char": _sized("NCHAR", min(length, 4000) if length else None),
        "string": "NVARCHAR(MAX)" if length is None or length > 4000 else f"NVARCHAR({length})",
        "text": "NVARCHAR(MAX)",
        "binary": "VARBINARY(MAX)" if length is None or length > 8000 else f"VARBINARY({length})",
        "date": "DATE",
        "time": "TIME",
        "timestamp": "DATETIME2",
        "timestamptz": "DATETIMEOFFSET",
        "uuid": "UNIQUEIDENTIFIER",
        "json": "NVARCHAR(MAX)",
    }[t["type"]]


def _oracle(t: Mapping[str, Any]) -> str:
    length = t.get("length")
    return {
        "smallint": "NUMBER(5)",
        "integer": "NUMBER(10)",
        "bigint": "NUMBER(19)",
        "decimal": _decimal("NUMBER", t),
        "float": "BINARY_FLOAT",
        "double": "BINARY_DOUBLE",
        "boolean": "NUMBER(1)",
        "char": _sized("CHAR", min(length, 2000) if length else None),
        "string": "CLOB" if length is None or length > 4000 else f"VARCHAR2({length})",
        "text": "CLOB",
        "binary": "BLOB" if length is None or length > 2000 else f"RAW({length})",
        "date": "DATE",
        "time": "INTERVAL DAY(0) TO SECOND(0)",
        "timestamp": "TIMESTAMP",
        "timestamptz": "TIMESTAMP WITH TIME ZONE",
        "uuid": "RAW(16)",
        "json": "CLOB",
    }[t["type"]]


def _snowflake(t: Mapping[str, Any]) -> str:
    return {
        "smallint": "SMALLINT",
        "integer": "INTEGER",
        "bigint": "BIGINT",
        "decimal": _decimal("NUMBER", t, "NUMBER(38,0)"),
        "float": "FLOAT",
        "double": "DOUBLE",
        "boolean": "BOOLEAN",
        "char": _sized("CHAR", t.get("length")),
        "string": _sized("VARCHAR", t.get("length")),
        "text": "VARCHAR",
        "binary": _sized("BINARY", t.get("length")),
        "date": "DATE",
        "time": "TIME",
        "timestamp": "TIMESTAMP_NTZ",
        "timestamptz": "TIMESTAMP_TZ",
        "uuid": "VARCHAR(36)",
        "json": "VARIANT",
    }[t["type"]]


def _bigquery_decimal(t: Mapping[str, Any]) -> str:
    precision, scale = t.get("precision"), t.get("scale") or 0
    if precision is None:
        return "NUMERIC"
    # NUMERIC holds up to 29 integer and 9 fractional digits; wider needs BIGNUMERIC.
    base = "NUMERIC" if precision - scale <= 29 and scale <= 9 else "BIGNUMERIC"
    return f"{base}({precision},{scale})"


def _bigquery(t: Mapping[str, Any]) -> str:
    return {
        "smallint": "INT64",
        "integer": "INT64",
        "bigint": "INT64",
        "decimal": _bigquery_decimal(t),
        "float": "FLOAT64",
        "double": "FLOAT64",
        "boolean": "BOOL",
        "char": _sized("STRING", t.get("length")),
        "string": _sized("STRING", t.get("length")),
        "text": "STRING",
        "binary": _sized("BYTES", t.get("length")),
        "date": "DATE",
        "time": "TIME",
        "timestamp": "DATETIME",
        "timestamptz": "TIMESTAMP",
        "uuid": "STRING",
        "json": "JSON",
    }[t["type"]]


_TYPES: dict[TargetPlatform, Callable[[Mapping[str, Any]], str]] = {
    "postgresql": _postgresql,
    "sqlserver": _sqlserver,
    "oracle": _oracle,
    "snowflake": _snowflake,
    "bigquery": _bigquery,
}


def translate_type(platform: TargetPlatform, data_type: Mapping[str, Any]) -> str:
    """The platform's column type for a neutral ``data_type``."""
    return _TYPES[platform](data_type)


# --- unknown-member values ------------------------------------------------------------

_NUMERIC = ("smallint", "integer", "bigint", "decimal", "float", "double")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")

_ISO = {
    "date": "1900-01-01",
    "time": "00:00:00",
    "timestamp": "1900-01-01 00:00:00",
    "timestamptz": "1900-01-01 00:00:00+00:00",
}
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _quote(platform: TargetPlatform, value: str) -> str:
    """``value`` as a single-quoted string literal. Snowflake and BigQuery read ``\\`` as an
    escape, so it is doubled first; BigQuery refuses ``''`` and takes ``\\'``."""
    if platform == "bigquery":
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    elif platform == "snowflake":
        escaped = value.replace("\\", "\\\\").replace("'", "''")
    else:
        escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _string(platform: TargetPlatform, value: str) -> str:
    text = _quote(platform, value)
    return "N" + text if platform == "sqlserver" else text


def _boolean(platform: TargetPlatform, value: bool) -> str:
    if platform in ("sqlserver", "oracle"):
        return "1" if value else "0"
    return "TRUE" if value else "FALSE"


def _temporal(platform: TargetPlatform, kind: str, value: str) -> str:
    plain = _quote(platform, value)
    if platform == "sqlserver":
        return plain
    if kind == "time":
        if platform == "oracle":
            return f"INTERVAL {_quote(platform, '0 ' + value)} DAY TO SECOND"
        return f"TIME {plain}"
    if kind == "timestamptz":
        if platform == "oracle":
            return f"TIMESTAMP {_quote(platform, value.replace('+00:00', ' +00:00'))}"
        if platform == "snowflake":
            return f"TO_TIMESTAMP_TZ({plain})"
        return f"TIMESTAMP {plain}" if platform == "bigquery" else f"TIMESTAMPTZ {plain}"
    # BigQuery's DATETIME is the neutral ``timestamp``; its TIMESTAMP carries a zone.
    timestamp = "DATETIME" if platform == "bigquery" else "TIMESTAMP"
    keyword = "DATE" if kind == "date" else timestamp
    return f"{keyword} {plain}"


def _fallback(platform: TargetPlatform, column: DdlColumn) -> str:
    """What a column holds in the unknown member when nobody set a value."""
    kind = column.data_type["type"]
    if kind in _NUMERIC:
        return "0"
    if kind == "boolean":
        return _boolean(platform, False)
    if kind in ("char", "string", "text"):
        length = column.data_type.get("length")
        return _string(platform, "Unknown"[:length] if length else "Unknown")
    if kind in _ISO:
        return _temporal(platform, kind, _ISO[kind])
    if kind == "uuid":
        return {
            "oracle": "HEXTORAW('00000000000000000000000000000000')",
            "sqlserver": f"CAST('{_ZERO_UUID}' AS UNIQUEIDENTIFIER)",
        }.get(platform, f"'{_ZERO_UUID}'")
    if kind == "binary":
        return {
            "postgresql": "'\\x00'",
            "sqlserver": "0x00",
            "oracle": "HEXTORAW('00')",
            "snowflake": "TO_BINARY('00')",
            "bigquery": "b'\\x00'",
        }[platform]
    # json
    return {"snowflake": "PARSE_JSON('{}')", "bigquery": "JSON '{}'"}.get(platform, "'{}'")


def _literal(platform: TargetPlatform, column: DdlColumn, value: Any) -> str:
    """A configured default, rendered for the column's type."""
    if value is None:
        return "NULL"
    kind = column.data_type["type"]
    if kind == "boolean":
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return _boolean(platform, True)
        if text in ("false", "0", "no"):
            return _boolean(platform, False)
        return _fallback(platform, column)
    if isinstance(value, bool):
        return _boolean(platform, value)
    if isinstance(value, int | float):
        return repr(value)
    if kind in _NUMERIC and _NUMBER.fullmatch(value.strip()):
        return value.strip()
    if kind in _ISO:
        return _temporal(platform, kind, value)
    return _string(platform, value)


_SCD_VALUES: dict[str, Callable[[TargetPlatform, DdlColumn], str]] = {
    "scd_valid_from": lambda p, c: _temporal(p, "timestamp", "1900-01-01 00:00:00"),
    "scd_valid_to": lambda p, c: _temporal(p, "timestamp", "9999-12-31 00:00:00"),
    "scd_current_flag": lambda p, c: _boolean(p, True),
    "row_hash": lambda p, c: _string(p, "unknown"),
}


def _unknown_value(platform: TargetPlatform, table: DdlTable, column: DdlColumn) -> str:
    member = table.unknown_member or {}
    defaults = member.get("defaults", {})
    if column.role == "sk":
        return str(member.get("surrogate_key", UNKNOWN_MEMBER_KEY))
    if column.name in defaults:
        return _literal(platform, column, defaults[column.name])
    if column.role in _SCD_VALUES:
        return _SCD_VALUES[column.role](platform, column)
    if column.is_nullable and column.role != "nk":
        return "NULL"
    return _fallback(platform, column)


# --- statements -----------------------------------------------------------------------


def _qualified(platform: TargetPlatform, schemas: Mapping[str, str], table: DdlTable) -> str:
    return f"{ident(platform, schemas[table.layer])}.{ident(platform, table.name)}"


def _create_schema(platform: TargetPlatform, name: str) -> str:
    quoted = ident(platform, name)
    if platform == "sqlserver":
        literal = name.replace("'", "''")
        command = f"CREATE SCHEMA {quoted}".replace("'", "''")
        return (
            f"IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'{literal}')\n"
            f"    EXEC('{command}');"
        )
    if platform == "oracle":
        if "\n" in name or "\r" in name:
            raise ValueError("A schema name cannot contain a line break.")
        return f"-- Oracle: schema {quoted} is a user; create it first (CREATE USER {quoted} ...)."
    return f"CREATE SCHEMA IF NOT EXISTS {quoted};"


def _create_table(platform: TargetPlatform, schemas: Mapping[str, str], table: DdlTable) -> str:
    lines = []
    for column in table.columns:
        null = "" if column.is_nullable else " NOT NULL"
        type_sql = translate_type(platform, column.data_type)
        lines.append(f"    {ident(platform, column.name)} {type_sql}{null}")
    keys = [c for c in table.columns if c.role == "sk"]
    if keys:
        cols = ", ".join(ident(platform, c.name) for c in keys)
        enforcement = " NOT ENFORCED" if platform == "bigquery" else ""
        lines.append(f"    PRIMARY KEY ({cols}){enforcement}")
    body = ",\n".join(lines)
    return f"CREATE TABLE {_qualified(platform, schemas, table)} (\n{body}\n);"


def _foreign_keys(
    platform: TargetPlatform,
    schemas: Mapping[str, str],
    table: DdlTable,
    by_id: Mapping[Any, DdlTable],
) -> list[str]:
    out = []
    for column in table.columns:
        target = by_id.get(column.references_table_id)
        if target is None:
            continue
        key = next((c for c in target.columns if c.role == "sk"), None)
        if key is None:
            continue
        name = ident(platform, _constraint_name(platform, "fk", table.name, column.name))
        enforcement = " NOT ENFORCED" if platform == "bigquery" else ""
        out.append(
            f"ALTER TABLE {_qualified(platform, schemas, table)} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({ident(platform, column.name)}) "
            f"REFERENCES {_qualified(platform, schemas, target)} "
            f"({ident(platform, key.name)}){enforcement};"
        )
    return out


def _unknown_insert(platform: TargetPlatform, schemas: Mapping[str, str], table: DdlTable) -> str:
    names = ", ".join(ident(platform, c.name) for c in table.columns)
    values = ", ".join(_unknown_value(platform, table, c) for c in table.columns)
    target = f"INSERT INTO {_qualified(platform, schemas, table)} ({names})"
    # Snowflake refuses functions such as PARSE_JSON inside VALUES.
    if platform == "snowflake":
        return f"{target}\nSELECT {values};"
    return f"{target}\nVALUES ({values});"


def _section(title: str, statements: Sequence[str]) -> str:
    return "\n\n".join([f"-- {title}", *statements]) if statements else ""


def generate_ddl(
    platform: TargetPlatform,
    schemas: Mapping[str, str],
    tables: Sequence[DdlTable],
    *,
    layers: Sequence[str] | None = None,
) -> str:
    """The DDL package of ``layers`` (default: every Layer that has tables).

    ``schemas`` maps each Layer to its physical schema (dataset) name. Output is
    deterministic: Layers in pipeline order, tables by name.
    """
    wanted = [layer for layer in LAYER_ORDER if layers is None or layer in layers]
    package = sorted(
        (t for t in tables if t.layer in wanted),
        key=lambda t: (LAYER_ORDER.index(t.layer), t.name.lower(), str(t.id)),
    )
    used = [layer for layer in wanted if any(t.layer == layer for t in package)]
    by_id = {t.id: t for t in package}
    foreign_keys = [fk for t in package for fk in _foreign_keys(platform, schemas, t, by_id)]
    inserts = [
        _unknown_insert(platform, schemas, t)
        for t in package
        if t.kind == "dimension" and t.unknown_member is not None
    ]
    sections = [
        _section("Schemas", [_create_schema(platform, schemas[layer]) for layer in used]),
        _section("Tables", [_create_table(platform, schemas, t) for t in package]),
        _section("Foreign keys", foreign_keys),
        _section("Unknown members", inserts),
    ]
    header = f"-- DAWAM DDL for {platform}; layers: {', '.join(used) or 'none'}"
    return "\n\n".join([header, *[s for s in sections if s]]) + "\n"
