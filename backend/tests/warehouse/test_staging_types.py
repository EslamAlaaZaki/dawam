"""Source type to staging type (spec §6.7): the translation table, its per-platform
differences and its explicit fallbacks. Pure functions, driven by tables."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse.model_service import neutral_type
from dawam.modules.warehouse.platforms import TARGET_PLATFORMS
from dawam.modules.warehouse.staging_types import translate_type


def neutral(kind, length=None, precision=None, scale=None):
    return {"type": kind, "length": length, "precision": precision, "scale": scale}


@pytest.mark.parametrize(
    ("source", "engine", "expected"),
    [
        ("integer", None, neutral("integer")),
        ("int4", "postgresql", neutral("integer")),
        ("smallint", None, neutral("smallint")),
        ("tinyint", "sqlserver", neutral("smallint")),
        ("int unsigned", "mysql", neutral("integer")),
        ("bigint", None, neutral("bigint")),
        ("bigserial", "postgresql", neutral("bigint")),
        ("numeric(12,2)", None, neutral("decimal", precision=12, scale=2)),
        ("decimal(10)", "mysql", neutral("decimal", precision=10, scale=0)),
        ("NUMBER(10,0)", "oracle", neutral("decimal", precision=10, scale=0)),
        ("money", "sqlserver", neutral("decimal", precision=19, scale=4)),
        ("real", None, neutral("float")),
        ("float", "sqlserver", neutral("double")),
        ("float(24)", "sqlserver", neutral("float")),
        ("double precision", "postgresql", neutral("double")),
        ("boolean", None, neutral("boolean")),
        ("bit", "sqlserver", neutral("boolean")),
        ("character varying(50)", "postgresql", neutral("string", length=50)),
        ("VARCHAR2(100)", "oracle", neutral("string", length=100)),
        ("nvarchar(200)", "sqlserver", neutral("string", length=200)),
        ("varchar(max)", "sqlserver", neutral("text")),
        ("character(3)", "postgresql", neutral("char", length=3)),
        ("char", None, neutral("char", length=1)),
        ("text", None, neutral("text")),
        ("CLOB", "oracle", neutral("text")),
        ("bytea", "postgresql", neutral("binary")),
        ("varbinary(16)", "sqlserver", neutral("binary", length=16)),
        ("date", "postgresql", neutral("date")),
        ("DATE", "oracle", neutral("timestamp")),
        ("time without time zone", "postgresql", neutral("time")),
        ("timestamp without time zone", "postgresql", neutral("timestamp")),
        ("timestamp(6)", "oracle", neutral("timestamp")),
        ("datetime2(7)", "sqlserver", neutral("timestamp")),
        ("timestamp with time zone", "postgresql", neutral("timestamptz")),
        ("TIMESTAMP(6) WITH TIME ZONE", "oracle", neutral("timestamptz")),
        ("datetimeoffset", "sqlserver", neutral("timestamptz")),
        ("timestamp", "sqlserver", neutral("binary", length=8)),
        ("uuid", "postgresql", neutral("uuid")),
        ("uniqueidentifier", "sqlserver", neutral("uuid")),
        ("jsonb", "postgresql", neutral("json")),
    ],
)
@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_plain_translations_are_clean_on_every_platform(platform, source, engine, expected):
    result = translate_type(platform, source, engine=engine)

    assert result.data_type == expected
    assert result.flags == ()


@pytest.mark.parametrize("platform", TARGET_PLATFORMS)
def test_every_translation_is_a_valid_neutral_type(platform):
    for source in ("numeric", "number(*)", "varchar(99999999)", "xmltype", "weird", "int[]", ""):
        result = translate_type(platform, source)
        assert neutral_type(result.data_type) == result.data_type


@pytest.mark.parametrize(
    ("platform", "scale"),
    [("postgresql", 10), ("sqlserver", 10), ("oracle", 10), ("snowflake", 10), ("bigquery", 9)],
)
def test_a_number_without_precision_is_a_wide_decimal_and_lossy(platform, scale):
    result = translate_type(platform, "NUMBER", engine="oracle")

    assert result.data_type == neutral("decimal", precision=38, scale=scale)
    assert [f.code for f in result.flags] == ["lossy_type"]


@pytest.mark.parametrize(
    ("source", "expected", "codes"),
    [
        ("NUMBER(*,0)", neutral("decimal", precision=38, scale=10), ["lossy_type"]),
        ("NUMBER(*)", neutral("decimal", precision=38, scale=10), ["lossy_type"]),
        ("NUMBER(3,5)", neutral("decimal", precision=3, scale=3), ["lossy_type"]),
        ("NUMBER(5,5)", neutral("decimal", precision=5, scale=5), []),
        ("NUMBER(10,-2)", neutral("decimal", precision=10, scale=0), ["lossy_type"]),
        ("VARCHAR2(100 CHAR)", neutral("string", length=100), []),
        ("VARCHAR2(100 BYTE)", neutral("string", length=100), []),
        ("NVARCHAR2(50)", neutral("string", length=50), []),
        ("CHAR(10 CHAR)", neutral("char", length=10), []),
    ],
)
def test_oracle_precision_and_length_semantics(source, expected, codes):
    result = translate_type("postgresql", source, engine="oracle")

    assert result.data_type == expected
    assert [f.code for f in result.flags] == codes


def test_a_decimal_wider_than_38_digits_is_capped_and_lossy():
    result = translate_type("postgresql", "numeric(50,5)")

    assert result.data_type == neutral("decimal", precision=38, scale=5)
    assert [f.code for f in result.flags] == ["lossy_type"]


def test_a_scale_over_the_platforms_is_capped_and_lossy():
    result = translate_type("bigquery", "numeric(20,15)")

    assert result.data_type == neutral("decimal", precision=20, scale=9)
    assert [f.code for f in result.flags] == ["lossy_type"]


@pytest.mark.parametrize(
    ("platform", "length", "kept"),
    [
        ("postgresql", 5000, "string"),
        ("sqlserver", 5000, "string"),
        ("sqlserver", 9000, "text"),
        ("oracle", 4000, "string"),
        ("oracle", 4001, "text"),
        ("snowflake", 100000, "string"),
    ],
)
def test_a_varchar_longer_than_the_platform_allows_becomes_text(platform, length, kept):
    result = translate_type(platform, f"varchar({length})")

    assert result.data_type["type"] == kept
    assert bool(result.flags) is (kept == "text")


@pytest.mark.parametrize(
    ("source", "engine", "expected", "code"),
    [
        ("XMLTYPE", "oracle", "text", "fallback_type"),
        ("xml", "sqlserver", "text", "fallback_type"),
        ("sql_variant", "sqlserver", "text", "fallback_type"),
        ("geometry", "sqlserver", "text", "fallback_type"),
        ("integer[]", "postgresql", "json", "fallback_type"),
        ("_int4", "postgresql", "json", "fallback_type"),
        ("interval", "postgresql", "string", "lossy_type"),
        ("enum('a','b')", "mysql", "string", "lossy_type"),
        ("time with time zone", "postgresql", "time", "lossy_type"),
        ("a_type_nobody_knows", None, "text", "fallback_type"),
        ("", None, "text", "fallback_type"),
    ],
)
def test_fallbacks_and_lossy_translations_are_flagged(source, engine, expected, code):
    result = translate_type("postgresql", source, engine=engine)

    assert result.data_type["type"] == expected
    assert [f.code for f in result.flags] == [code]
    assert result.flags[0].message
