"""Names of Staging Tables and columns (spec §6.7, stories 82, 84, 84a).

Pure functions: source names and the target platform in, valid identifiers and review
flags out. Deterministic and independent of ordering: a name collides when two objects
reach the same lower-cased identifier, and then every one of them still to be named gets
the stable hash suffix (first 6 hex characters of SHA-256 over the Source Object's id).
Placeholder numbers (``tbl_007``, ``col_017``) come from the Source Object, assigned once.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .platforms import TargetPlatform, is_reserved_word, max_identifier_length

FlagCode = Literal["placeholder", "truncated", "collision", "lossy_type", "fallback_type"]

HASH_LENGTH = 6
RESERVED_SUFFIX = "_col"
_NOT_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Flag:
    code: FlagCode
    message: str


@dataclass(frozen=True)
class NameInput:
    id: str
    """The Source Object's id; the hash suffix is taken over it."""
    name: str
    placeholder_no: int | None = None


@dataclass(frozen=True)
class Named:
    name: str
    flags: tuple[Flag, ...] = ()


def stable_hash(object_id: str, length: int = HASH_LENGTH) -> str:
    return hashlib.sha256(object_id.encode()).hexdigest()[:length]


def sanitize(name: str) -> str | None:
    """Lower-cased, ASCII, words joined by ``_``; ``None`` when nothing Latin is left to
    carry the name (non-Latin script, or only symbols), which needs a placeholder."""
    folded: list[str] = []
    for char in unicodedata.normalize("NFKD", name):
        if unicodedata.combining(char):
            continue
        if char.isascii():
            folded.append(char)
        elif char.isalpha() or char.isdigit():
            return None
        else:
            folded.append(" ")
    text = _NOT_WORD.sub("_", "".join(folded).lower()).strip("_")
    return text or None


def needs_placeholder(name: str) -> bool:
    return sanitize(name) is None


def _limit(platform: TargetPlatform, max_length: int | None) -> int:
    limit = max_identifier_length(platform)
    return limit if max_length is None else min(limit, max_length)


def _placeholder(prefix: str, number: int) -> str:
    return f"{prefix}_{number:03d}"


def _fit(base: str, object_id: str, limit: int, taken: bool) -> tuple[str, list[Flag]]:
    """``base`` shortened to ``limit`` bytes with the hash suffix when it is too long or
    ``taken``; the flags say why."""
    flags: list[Flag] = []
    suffixed = False
    if len(base.encode()) > limit:
        flags.append(Flag("truncated", f"The name is over the platform's {limit}-byte limit."))
        suffixed = True
    if taken:
        flags.append(Flag("collision", "The name collides with another one after sanitising."))
        suffixed = True
    if not suffixed:
        return base, flags
    suffix = "_" + stable_hash(object_id)
    return base[: limit - len(suffix)].rstrip("_") + suffix, flags


def _resolve(
    items: list[tuple[NameInput, str, list[Flag]]],
    limit: int,
    taken: set[str],
) -> dict[str, Named]:
    """Name ``items`` (id, wanted name, flags so far) among themselves and the ``taken``
    names; any that share a (lower-case) name, or meet a taken one, all get the suffix."""
    groups: dict[str, int] = defaultdict(int)
    for _, base, _flags in items:
        groups[base.lower()] += 1
    result: dict[str, Named] = {}
    used = {t.lower() for t in taken}
    for item, base, flags in items:
        key = base.lower()
        clash = key in used or groups[key] > 1
        name, extra = _fit(base, item.id, limit, clash)
        length = HASH_LENGTH
        while name.lower() in used:  # a hash collision is vanishingly rare; widen to escape
            length += 2
            name = base[: limit - length - 1].rstrip("_") + "_" + stable_hash(item.id, length)
        used.add(name.lower())
        result[item.id] = Named(name, tuple(flags + extra))
    return result


def table_names(
    platform: TargetPlatform,
    *,
    tables: Iterable[tuple[str, NameInput, NameInput]],
    taken: Iterable[str] = (),
    max_length: int | None = None,
) -> dict[str, Named]:
    """``stg_<system code>_<database schema>_<table>`` for each ``(system code, schema,
    table)``, keyed by the table's id. A non-Latin schema or table name uses its placeholder
    number (a schema's falls back to its hash); ``taken`` holds names that already exist;
    ``max_length`` caps the platform's identifier limit (where a column is narrower)."""
    limit = _limit(platform, max_length)
    items: list[tuple[NameInput, str, list[Flag]]] = []
    for system_code, schema, table in tables:
        flags: list[Flag] = []
        schema_part = sanitize(schema.name)
        if schema_part is None:
            schema_part = "sch_" + stable_hash(schema.id)
            flags.append(Flag("placeholder", f"The schema name {schema.name!r} is not Latin."))
        table_part = sanitize(table.name)
        if table_part is None:
            if table.placeholder_no is None:
                raise ValueError(f"table {table.id} needs a placeholder number")
            table_part = _placeholder("tbl", table.placeholder_no)
            flags.append(Flag("placeholder", f"The table name {table.name!r} is not Latin."))
        base = f"stg_{system_code.lower()}_{schema_part}_{table_part}"
        items.append((table, base, flags))
    return _resolve(items, limit, set(taken))


def column_names(
    platform: TargetPlatform,
    *,
    columns: Iterable[NameInput],
    taken: Iterable[str] = (),
    max_length: int | None = None,
) -> dict[str, Named]:
    """A column name: sanitised, a placeholder when non-Latin, a suffix when reserved on
    the platform or starting with a digit; ``taken`` holds names in the table already
    (the audit columns, columns generated earlier)."""
    limit = _limit(platform, max_length)
    items: list[tuple[NameInput, str, list[Flag]]] = []
    for column in columns:
        flags: list[Flag] = []
        base = sanitize(column.name)
        if base is None:
            if column.placeholder_no is None:
                raise ValueError(f"column {column.id} needs a placeholder number")
            base = _placeholder("col", column.placeholder_no)
            flags.append(Flag("placeholder", f"The column name {column.name!r} is not Latin."))
        if base[0].isdigit():
            base = "c_" + base
        if is_reserved_word(platform, base):
            base += RESERVED_SUFFIX
        items.append((column, base, flags))
    return _resolve(items, limit, set(taken))
