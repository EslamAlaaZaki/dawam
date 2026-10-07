"""Value-based PII scoring (spec §6.12): turn the values sampled from one column into at
most one finding, keeping only the match ratio.

Pure: it reads no source and no database. ``score_column`` tests the sampled values in
memory against every validator of ``dawam.platform.pii_validators`` and returns counts
and a score; the values never leave this function, so a finding (and everything written
to the database, logs and job output after it) holds none.

**Confidence** combines the name match and the value match. A validator's value
confidence is its match ratio times its weight (a checksum is worth 1, a bare 10-digit
pattern much less). If the column's name also matches the same rule, the two combine as
independent evidence, ``1 - (1 - name) * (1 - value)``, so a vague pattern on a column
named like it is confident while the same pattern alone is not. Only a validator that
matched at least ``MIN_MATCH_RATIO`` of a column's values counts, and a finding below
``REVIEW_THRESHOLD`` is not recorded.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from dawam.platform.pii_validators import VALIDATORS, match_ratio

from .pii_rules import NAME_CONFIDENCE_FLOOR, match_name

DEFAULT_SAMPLE_SIZE = 1000
MAX_SAMPLE_SIZE = 10_000
MIN_SAMPLED_VALUES = 3
"""Fewer non-null values than this say nothing about a column."""
MIN_MATCH_RATIO = 0.3
REVIEW_THRESHOLD = NAME_CONFIDENCE_FLOOR
"""A finding at or above this enters the review queue (spec §6.12)."""


@dataclass(frozen=True)
class ValueFinding:
    rule: str
    category: str
    confidence: float
    evidence: str
    """Counts and the rule only, never a value."""
    matched: int
    total: int


def combine(name_confidence: float, value_confidence: float) -> float:
    """Independent name and value evidence, as one confidence."""
    return 1 - (1 - name_confidence) * (1 - value_confidence)


def score_column(
    column: str, values: Iterable[Any], *, today: date | None = None
) -> ValueFinding | None:
    """The best finding for one column's sampled ``values``, or ``None``."""
    sampled = [v for v in values if v is not None]
    if len(sampled) < MIN_SAMPLED_VALUES:
        return None
    name = match_name(column)
    best: ValueFinding | None = None
    for rule, validator in VALIDATORS.items():
        found = match_ratio(rule, sampled, today=today)
        if found is None or found.ratio < MIN_MATCH_RATIO:
            continue
        name_confidence = name.confidence if name is not None and name.rule == rule else 0.0
        confidence = combine(name_confidence, found.ratio * validator.weight)
        if confidence < REVIEW_THRESHOLD or (best is not None and confidence <= best.confidence):
            continue
        evidence = (
            f'{found.matched} of {found.total} sampled values in "{column}" pass the '
            f"{rule} check (match ratio {found.ratio:.0%})"
        )
        if name_confidence:
            evidence += f"; the column name also matches the {rule} name rule"
        best = ValueFinding(
            rule=rule,
            category=validator.category,
            confidence=round(confidence, 4),
            evidence=evidence + ".",
            matched=found.matched,
            total=found.total,
        )
    return best
