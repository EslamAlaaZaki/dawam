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

from .pii_rules import DEFAULT_RULES, NAME_CONFIDENCE_FLOOR, RuleSet, match_name

DEFAULT_SAMPLE_SIZE = 1000
MAX_SAMPLE_SIZE = 10_000
MIN_SAMPLED_VALUES = 3
"""Fewer non-null values than this say nothing about a column."""
MIN_MATCH_RATIO = 0.3
CUSTOM_PATTERN_WEIGHT = 0.9
"""What a full match of a custom rule's regex is worth on its own: the Owner wrote it for
their own identifiers, but a regex has no checksum."""
MAX_VALUE_LENGTH = 256
"""A longer value is cut before a custom regex sees it, which bounds the time it can take."""
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
    column: str,
    values: Iterable[Any],
    *,
    today: date | None = None,
    rules: RuleSet = DEFAULT_RULES,
) -> ValueFinding | None:
    """The best finding for one column's sampled ``values``, or ``None``. ``rules`` are the
    Workspace's: its disabled built-in rules are skipped and its custom regexes are tested
    too."""
    sampled = [v for v in values if v is not None]
    if len(sampled) < MIN_SAMPLED_VALUES:
        return None
    name = match_name(column, rules)
    candidates: list[tuple[str, str, float, int, int]] = []
    """Rule id, category, weight, matched and total."""
    for rule, validator in VALIDATORS.items():
        if rule in rules.disabled:
            continue
        found = match_ratio(rule, sampled, today=today)
        if found is not None:
            candidates.append(
                (rule, validator.category, validator.weight, found.matched, found.total)
            )
    for custom in rules.custom:
        if custom.pattern is not None:
            matched = sum(
                custom.pattern.fullmatch(str(v)[:MAX_VALUE_LENGTH]) is not None for v in sampled
            )
            candidates.append(
                (custom.id, custom.category, CUSTOM_PATTERN_WEIGHT, matched, len(sampled))
            )
    best: ValueFinding | None = None
    for rule, category, weight, matched, total in candidates:
        ratio = matched / total
        if ratio < MIN_MATCH_RATIO:
            continue
        name_confidence = name.confidence if name is not None and name.rule == rule else 0.0
        confidence = combine(name_confidence, ratio * weight)
        if confidence < REVIEW_THRESHOLD or (best is not None and confidence <= best.confidence):
            continue
        evidence = (
            f'{matched} of {total} sampled values in "{column}" pass the '
            f"{rule} check (match ratio {ratio:.0%})"
        )
        if name_confidence:
            evidence += f"; the column name also matches the {rule} name rule"
        best = ValueFinding(
            rule=rule,
            category=category,
            confidence=round(confidence, 4),
            evidence=evidence + ".",
            matched=matched,
            total=total,
        )
    return best
