"""PII value validators (spec §6.12): pure, Saudi-focused checks on one value.

Regex and checksum only, no AI. Nothing here reads a database, logs or keeps a value, so
the value-based PII scan, the query-time check on AI results (§6.8) and redaction all use
the same rules. A validator never raises: a value it cannot read (``None``, bytes, a float,
anything very long) simply does not match.

``VALIDATORS`` is keyed by rule id; the ids are the ones of the name rules in
``dawam.modules.sources.internal.pii_rules`` so a name match and a value match on the same
rule combine into one finding. ``weight`` says how much a full match is worth on its own:
a checksum or a distinctive shape is 1; a pattern many non-PII columns also satisfy (any
10-digit number, any past date) counts for less, so it only matters next to a name match.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

MAX_VALUE_LENGTH = 256
"""A longer value is free text, never an identifier."""

MAX_AGE_YEARS = 120

_SEPARATORS = re.compile(r"[\s\-().]")


@dataclass(frozen=True)
class Validator:
    id: str
    category: str
    weight: float
    check: Callable[[Any, date], bool]


@dataclass(frozen=True)
class MatchRatio:
    matched: int
    total: int
    """The non-null values tested."""

    @property
    def ratio(self) -> float:
        return self.matched / self.total


def _text(value: Any) -> str | None:
    """The value as text, or ``None`` if it cannot be an identifier."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and len(value) <= MAX_VALUE_LENGTH:
        return value.strip()
    return None


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _id_check(first: str) -> Callable[[Any, date], bool]:
    pattern = re.compile(rf"{first}\d{{9}}")

    def check(value: Any, today: date) -> bool:
        text = _text(value)
        return text is not None and pattern.fullmatch(text) is not None and _luhn(text)

    return check


_MOBILE = re.compile(r"(?:0|(?:\+|00)?966)5\d{8}")


def _mobile(value: Any, today: date) -> bool:
    text = _text(value)
    if text is None:
        return False
    compact = _SEPARATORS.sub("", text)
    return _MOBILE.fullmatch(compact) is not None


_IBAN = re.compile(r"SA\d{2}[0-9A-Z]{20}")


def _iban(value: Any, today: date) -> bool:
    text = _text(value)
    if text is None:
        return False
    compact = re.sub(r"[\s\-]", "", text).upper()
    if _IBAN.fullmatch(compact) is None:
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(char, 36)) for char in rearranged)
    return int(number) % 97 == 1


_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}"
)


def _email(value: Any, today: date) -> bool:
    text = _text(value)
    return text is not None and len(text) <= 254 and _EMAIL.fullmatch(text) is not None


def _card(value: Any, today: date) -> bool:
    text = _text(value)
    if text is None:
        return False
    compact = re.sub(r"[\s\-]", "", text)
    return (
        compact.isascii()
        and compact.isdigit()
        and 13 <= len(compact) <= 19
        and len(set(compact)) > 1
        and _luhn(compact)
    )


_COMMERCIAL_REGISTRATION = re.compile(r"[1-57]\d{9}")


def _commercial_registration(value: Any, today: date) -> bool:
    text = _text(value)
    return text is not None and _COMMERCIAL_REGISTRATION.fullmatch(text) is not None


_ISO_DATE = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:[T ].*)?")
_DAY_FIRST_DATE = re.compile(r"(\d{2})[-/](\d{2})[-/](\d{4})")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) > MAX_VALUE_LENGTH:
        return None
    text = value.strip()
    parts: tuple[str, str, str] | None = None
    if found := _ISO_DATE.fullmatch(text):
        parts = (found[1], found[2], found[3])
    elif found := _DAY_FIRST_DATE.fullmatch(text):
        parts = (found[3], found[2], found[1])
    if parts is None:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _birth_date(value: Any, today: date) -> bool:
    """A date whose implied age is a human one: not in the future, at most 120 years."""
    day = _as_date(value)
    if day is None or day > today:
        return False
    return today.year - day.year < MAX_AGE_YEARS or (
        today.year - day.year == MAX_AGE_YEARS and (day.month, day.day) >= (today.month, today.day)
    )


def _ip_address(value: Any, today: date) -> bool:
    text = _text(value)
    if text is None or isinstance(value, int):
        return False
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


_ALL: tuple[Validator, ...] = (
    Validator("national_id", "direct_identifier", 1.0, _id_check("1")),
    Validator("iqama", "direct_identifier", 1.0, _id_check("2")),
    Validator("phone", "direct_identifier", 1.0, _mobile),
    Validator("iban", "financial", 1.0, _iban),
    Validator("email", "direct_identifier", 1.0, _email),
    Validator("card_number", "financial", 1.0, _card),
    Validator("ip_address", "direct_identifier", 1.0, _ip_address),
    Validator("commercial_registration", "quasi_identifier", 0.4, _commercial_registration),
    Validator("birth_date", "quasi_identifier", 0.3, _birth_date),
)

VALIDATORS: dict[str, Validator] = {v.id: v for v in _ALL}
"""Every validator by rule id, most specific first (the order ties are broken in)."""


def validate(rule: str, value: Any, *, today: date | None = None) -> bool:
    """Whether ``value`` passes the validator ``rule``. Never raises on a odd value."""
    try:
        return VALIDATORS[rule].check(value, today or date.today())
    except (ValueError, OverflowError):
        return False


def match_ratio(
    rule: str, values: Iterable[Any], *, today: date | None = None
) -> MatchRatio | None:
    """How many of the non-null ``values`` pass ``rule``; ``None`` if there were none.
    Only the counts leave this function."""
    day = today or date.today()
    matched = total = 0
    for value in values:
        if value is None:
            continue
        total += 1
        if validate(rule, value, today=day):
            matched += 1
    return MatchRatio(matched, total) if total else None
