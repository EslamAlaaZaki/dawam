"""The Profiling page's rules (spec §6.5, story 57a): what a column's latest profile
flags, and how a table's row estimate grows between Snapshots.

Findings: one per column, the first matching rule wins. A Protected Column gets only the
null rules, so a finding never carries one of its values.

Growth: linear from the table's first row estimate to its latest. A drop of more than
``DROP_SHARE`` between consecutive Snapshots (a purge or a truncate) restarts the rate at
that Snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MOSTLY_NULL_PCT = 50.0
"""Above this null share a column is mostly null (the Profile tab warns at the same)."""
DROP_SHARE = 0.2
DAYS_PER_MONTH = 30.44

FindingKind = Literal["all_null", "mostly_null", "constant", "unique_not_key"]


@dataclass(frozen=True)
class ColumnFacts:
    row_count: int
    null_pct: float
    distinct_count: int | None
    row_cap: int
    min: str | None
    is_protected: bool
    is_key: bool
    """In a single-column primary key or unique constraint or index of the Source Schema."""


@dataclass(frozen=True)
class ColumnFinding:
    kind: FindingKind
    null_pct: float
    row_count: int
    capped: bool
    """The sample hit its cap: a unique column is unique only in the sampled rows."""
    value: str | None
    """The one value of a constant column; never set for a Protected Column."""


def finding_of(facts: ColumnFacts) -> ColumnFinding | None:
    """The column's finding, or ``None`` when no rule matches."""

    def found(kind: FindingKind, value: str | None = None) -> ColumnFinding:
        capped = facts.row_count >= facts.row_cap
        return ColumnFinding(kind, facts.null_pct, facts.row_count, capped, value)

    if facts.row_count > 0 and facts.null_pct >= 100:
        return found("all_null")
    if facts.null_pct > MOSTLY_NULL_PCT:
        return found("mostly_null")
    if facts.is_protected:
        return None
    distinct = facts.distinct_count
    if distinct == 1 and facts.row_count > 0:
        return found("constant", facts.min)
    if distinct is not None and distinct == facts.row_count > 0 and not facts.is_key:
        return found("unique_not_key")
    return None


@dataclass(frozen=True)
class Point:
    taken_at: datetime
    row_estimate: int


@dataclass(frozen=True)
class Growth:
    first: int
    latest: int
    change_pct: float | None
    """From the first estimate to the latest; ``None`` when the first is zero."""
    per_month: float
    """Rows per month, from ``dropped_at`` on when the table dropped."""
    in_12_months: float
    """The latest estimate plus twelve months at ``per_month``, never below zero."""
    dropped_at: datetime | None
    """The Snapshot of the table's last drop of more than ``DROP_SHARE``."""


def growth_of(points: Sequence[Point]) -> Growth | None:
    """The table's growth over ``points`` (oldest first), or ``None`` with fewer than two
    points or no change."""
    if len(points) < 2 or len({p.row_estimate for p in points}) == 1:
        return None
    base = 0
    for i in range(1, len(points)):
        if points[i].row_estimate < points[i - 1].row_estimate * (1 - DROP_SHARE):
            base = i
    first, latest = points[0].row_estimate, points[-1].row_estimate
    days = (points[-1].taken_at - points[base].taken_at).total_seconds() / 86_400
    per_month = (latest - points[base].row_estimate) / days * DAYS_PER_MONTH if days else 0.0
    return Growth(
        first=first,
        latest=latest,
        change_pct=(latest - first) / first * 100 if first else None,
        per_month=per_month,
        in_12_months=max(0.0, latest + per_month * 12),
        dropped_at=points[base].taken_at if base else None,
    )
