"""Column profiling statements and pattern detection (spec §6.5, stories 55-57).

Every engine's Connector builds its ``profile_column`` from here: the SQL differs only in
quoting, the row-limit clause and the length function (``Dialect``), and the statements
run through the Connector's own session, so they keep its read-only transaction,
statement timeout and allowed-schema scope.

Sampling is ``LIMIT``-based: statistics come from the first ``row_cap`` rows of the
table, so a production source is never scanned in full. Only aggregates leave this
module, plus (when the caller asks) the top-N values; patterns are detected in memory
from a small sample of distinct values and only the pattern *names* are kept.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .connector import ColumnStats, QueryResult

TOP_N = 10
PATTERN_SAMPLE = 200
PATTERN_SHARE = 0.8
"""A pattern is reported when at least this share of the sampled distinct values match."""

Run = Callable[[str, int], QueryResult]


@dataclass(frozen=True)
class Dialect:
    quote: Callable[[str], str]
    limit: Callable[[str, int], str]
    """``limit(select_of_the_column, n)``: the SELECT limited to its first ``n`` rows."""
    length: Callable[[str], str]


def _ansi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _backtick(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


DIALECTS: dict[str, Dialect] = {
    "postgresql": Dialect(
        _ansi, lambda select, n: f"{select} LIMIT {n}", lambda c: f"length(CAST({c} AS text))"
    ),
    "mysql": Dialect(
        _backtick, lambda select, n: f"{select} LIMIT {n}", lambda c: f"CHAR_LENGTH({c})"
    ),
    "sqlserver": Dialect(
        _bracket,
        lambda select, n: select.replace("SELECT ", f"SELECT TOP ({n}) ", 1),
        lambda c: f"LEN({c})",
    ),
    "oracle": Dialect(
        _ansi, lambda select, n: f"{select} FETCH FIRST {n} ROWS ONLY", lambda c: f"LENGTH({c})"
    ),
}

_TEXT = re.compile(r"char|text|string|uuid|citext|^name$", re.I)
_ORDERED = re.compile(
    r"int|serial|numeric|decimal|number|float|double|real|money|date|time|year", re.I
)
_UNCOMPARABLE = re.compile(
    r"json|xml|clob|blob|lob$|image|ntext|geometry|geography|\[\]|array|bytea|binary|raw"
    r"|point|polygon|circle|hstore|tsvector|variant|bit",
    re.I,
)


def kind_of(data_type: str) -> str:
    """``text``, ``ordered`` (numbers, dates) or ``other`` (no min/max, distinct or top-N:
    the engine cannot compare it), from a column's data type."""
    if _UNCOMPARABLE.search(data_type):
        return "other"
    if _TEXT.search(data_type):
        return "text"
    if _ORDERED.search(data_type):
        return "ordered"
    return "other"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal | int | float) else None


def _shown(value: Any) -> str | None:
    return None if value is None else str(value)


def profile_column(
    run: Run,
    engine: str,
    schema: str,
    table: str,
    column: str,
    data_type: str,
    *,
    row_cap: int,
    top_n: bool,
    with_min_max: bool,
) -> ColumnStats:
    """Profile one column over the table's first ``row_cap`` rows. ``with_min_max`` and
    ``top_n`` are the caller's decisions (a Protected Column gets neither)."""
    dialect = DIALECTS[engine]
    kind = kind_of(data_type)
    col = dialect.quote(column)
    base = f"SELECT {col} AS v FROM {dialect.quote(schema)}.{dialect.quote(table)}"
    sample = dialect.limit(base, row_cap)
    distinct = "COUNT(DISTINCT v)" if kind != "other" else "NULL"
    low = "MIN(v)" if with_min_max and kind != "other" else "NULL"
    high = "MAX(v)" if with_min_max and kind != "other" else "NULL"
    avg = f"AVG({dialect.length('v')})" if kind == "text" else "NULL"
    longest = f"MAX({dialect.length('v')})" if kind == "text" else "NULL"
    [row] = run(
        f"SELECT COUNT(*), COUNT(v), {distinct}, {low}, {high}, {avg}, {longest} FROM ({sample}) s",
        1,
    ).rows
    total, non_null, distinct_count, minimum, maximum, avg_len, max_len = row
    top_values: list[tuple[str, int]] | None = None
    if top_n and kind != "other" and non_null:
        found = run(
            f"SELECT v, COUNT(*) AS n FROM ({sample}) s WHERE v IS NOT NULL "
            f"GROUP BY v ORDER BY n DESC, v",
            TOP_N,
        )
        top_values = [(str(value), int(count)) for value, count in found.rows]
    patterns: list[str] = []
    if kind == "text" and non_null:
        values = run(
            f"SELECT DISTINCT v FROM ({sample}) s WHERE v IS NOT NULL", PATTERN_SAMPLE
        ).rows
        patterns = detect_patterns([str(r[0]) for r in values])
    total = int(total)
    return ColumnStats(
        row_count=total,
        null_count=total - int(non_null),
        distinct_count=None if distinct_count is None else int(distinct_count),
        minimum=_shown(minimum),
        maximum=_shown(maximum),
        avg_length=_number(avg_len),
        max_length=None if max_len is None else int(max_len),
        top_values=top_values,
        patterns=patterns,
    )


PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://\S+", re.I),
    "uuid": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
    "ipv4": re.compile(r"(\d{1,3})(\.\d{1,3}){3}"),
    "iso_date": re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?.*)?"),
    "iban": re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}"),
}
_PHONE = re.compile(r"\+?[\d\s().-]{7,20}")


def _is_phone(value: str) -> bool:
    return bool(_PHONE.fullmatch(value)) and 7 <= sum(c.isdigit() for c in value) <= 15


def detect_patterns(values: list[str]) -> list[str]:
    """Names of the patterns most of ``values`` follow. Values are never returned."""
    values = [v.strip() for v in values if v.strip()]
    if not values:
        return []
    found = [
        name
        for name, pattern in PATTERNS.items()
        if sum(bool(pattern.fullmatch(v)) for v in values) / len(values) >= PATTERN_SHARE
    ]
    if "iso_date" not in found and sum(map(_is_phone, values)) / len(values) >= PATTERN_SHARE:
        found.append("phone")
    return found
