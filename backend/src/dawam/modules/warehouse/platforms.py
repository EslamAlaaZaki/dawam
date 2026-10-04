"""What each target platform allows in an identifier (spec §6.9, story 87).

The facts other modules need to generate valid names: how long an identifier may be
and which words it may not be (staging generation suffixes a reserved word, and
shortens a name over the limit with a stable hash). They live here, once, so no module
keeps its own copy.

The reserved-word lists are the words each platform refuses as an unquoted
identifier. They are deliberately the conservative union of the platform's documented
reserved keywords and the SQL standard's, so a name that passes is safe unquoted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

TargetPlatform = Literal["postgresql", "sqlserver", "oracle", "snowflake", "bigquery"]
TARGET_PLATFORMS: tuple[TargetPlatform, ...] = get_args(TargetPlatform)


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_SQL_STANDARD = _words("""
    all and any as asc between by case cast check column constraint create cross
    current_date current_time current_timestamp current_user default delete desc distinct
    drop else end except exists false for foreign from full group having in inner insert
    intersect into is join key left like limit not null on or order outer primary
    references right select set some table then to true union unique update user using
    values when where with
    """)

_PER_PLATFORM: dict[TargetPlatform, frozenset[str]] = {
    "postgresql": _words(
        "analyse analyze array asymmetric authorization binary both collate concurrently "
        "do fetch freeze grant ilike initially isnull lateral leading localtime "
        "localtimestamp natural notnull offset only placing returning session_user "
        "similar symmetric tablesample trailing variadic verbose window"
    ),
    "sqlserver": _words(
        "add alter backup begin break browse bulk cascade checkpoint clustered coalesce "
        "commit compute contains continue convert current cursor database dbcc "
        "deallocate declare deny disk distributed double dump errlvl escape exec execute "
        "external fetch file fillfactor function goto grant holdlock identity "
        "identity_insert identitycol if index kill lineno load merge national nocheck "
        "nonclustered nullif of off offsets open opendatasource openquery openrowset "
        "openxml option over percent pivot plan precision print proc procedure public "
        "raiserror read readtext reconfigure replication restore restrict return revert "
        "revoke rollback rowcount rowguidcol rule save schema securityaudit "
        "semantickeyphrasetable session_user setuser shutdown statistics system_user "
        "tablesample textsize top tran transaction trigger truncate try_convert tsequal "
        "unpivot updatetext use view waitfor while within writetext"
    ),
    "oracle": _words(
        "access add audit level lock long maxextents minus mlslabel mode modify "
        "noaudit nowait number of offline online option raw rename resource row rowid "
        "rownum rows session share size smallint start successful synonym sysdate "
        "trigger uid validate varchar varchar2 view whenever"
    ),
    "snowflake": _words(
        "account connection database gscluster issue organization qualify regexp "
        "rlike sample schema trigger try_cast"
    ),
    "bigquery": _words(
        "assert_rows_modified at collate contains cube current cursor define enum "
        "escape exclude extract fetch following for grouping groups hash ignore "
        "interval lateral lookup merge natural new no nulls of over partition preceding "
        "proto range recursive respect rollup rows struct tablesample treat unbounded "
        "window within"
    ),
}


@dataclass(frozen=True)
class PlatformProfile:
    """One target platform's identifier rules."""

    platform: TargetPlatform
    label: str
    max_identifier_length: int
    """The longest table, column or schema name, in bytes (spec §6.9: PostgreSQL 63,
    Oracle 128, SQL Server 128). For BigQuery, the tightest of its limits (columns)."""
    schema_term: Literal["schema", "dataset"]
    """What the platform calls a Layer's physical home."""
    reserved_words: frozenset[str]
    """Lower-cased; compare with ``is_reserved_word``."""


PLATFORM_PROFILES: dict[TargetPlatform, PlatformProfile] = {
    "postgresql": PlatformProfile(
        "postgresql", "PostgreSQL", 63, "schema", _SQL_STANDARD | _PER_PLATFORM["postgresql"]
    ),
    "sqlserver": PlatformProfile(
        "sqlserver", "SQL Server", 128, "schema", _SQL_STANDARD | _PER_PLATFORM["sqlserver"]
    ),
    "oracle": PlatformProfile(
        "oracle", "Oracle", 128, "schema", _SQL_STANDARD | _PER_PLATFORM["oracle"]
    ),
    "snowflake": PlatformProfile(
        "snowflake", "Snowflake", 255, "schema", _SQL_STANDARD | _PER_PLATFORM["snowflake"]
    ),
    "bigquery": PlatformProfile(
        "bigquery", "BigQuery", 300, "dataset", _SQL_STANDARD | _PER_PLATFORM["bigquery"]
    ),
}

SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""An identifier every platform accepts unquoted: letters, digits and underscores."""


def platform_profile(platform: TargetPlatform) -> PlatformProfile:
    return PLATFORM_PROFILES[platform]


def max_identifier_length(platform: TargetPlatform) -> int:
    return PLATFORM_PROFILES[platform].max_identifier_length


def is_reserved_word(platform: TargetPlatform, name: str) -> bool:
    """Whether ``name`` (any case) is reserved on ``platform``."""
    return name.lower() in PLATFORM_PROFILES[platform].reserved_words
