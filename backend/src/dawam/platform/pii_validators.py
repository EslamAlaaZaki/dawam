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
import unicodedata
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


def _ascii_digits(text: str) -> str:
    """Arabic-Indic and other Unicode decimal digits as ASCII ones (Saudi data uses them)."""
    if text.isascii():
        return text
    return "".join(str(d) if (d := unicodedata.decimal(c, -1)) >= 0 else c for c in text)


def _text(value: Any) -> str | None:
    """The value as text, or ``None`` if it cannot be an identifier."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and len(value) <= MAX_VALUE_LENGTH:
        return _ascii_digits(value.strip())
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
    r"[\w.%+\-]+@[^\W_](?:[\w\-]*[^\W_])?"
    r"(?:\.[^\W_](?:[\w\-]*[^\W_])?)*\.[^\W\d_]{2,}"
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
    except Exception:  # a validator bug must never fail a scan or echo a value
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


# -- query-time checks and redaction (spec §6.8) -------------------------------------------

STRONG_RULES: tuple[str, ...] = tuple(rule for rule, v in VALIDATORS.items() if v.weight >= 1.0)
"""The rules whose match is conclusive on its own (a checksum or a distinctive shape): the
ones that mask a result column and redact model-written text. A bare date or 10-digit number
would mask half of any query result."""


def first_match(value: Any, *, today: date | None = None) -> str | None:
    """The first strong rule ``value`` passes, or ``None``. Only the rule id leaves."""
    day = today or date.today()
    for rule in STRONG_RULES:
        if validate(rule, value, today=day):
            return rule
    return None


_INVISIBLE = "​‌‍⁠﻿­"
"""Zero-width characters a writer can slip into an identifier to defeat a pattern."""
_JOINER = rf"[\s\-().{_INVISIBLE}]?"
_EMAIL_IN_TEXT = re.compile(_EMAIL.pattern)
_IPV4_IN_TEXT = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6_IN_TEXT = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
_IBAN_IN_TEXT = re.compile(
    rf"(?<![A-Za-z0-9])[Ss][Aa](?:{_JOINER}[0-9A-Za-z]){{22}}(?![A-Za-z0-9])"
)
MIN_NUMBER_DIGITS = 9
MAX_NUMBER_DIGITS = 19
_NUMBER_RULES = ("national_id", "iqama", "phone", "card_number")
_DIGITS_IN_TEXT = re.compile(rf"(?<!\d)\+?\d(?:{_JOINER}\d){{{MIN_NUMBER_DIGITS - 1},}}(?!\d)")


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _strip_invisible(text: str) -> str:
    return text.translate(dict.fromkeys(map(ord, _INVISIBLE)))


def redact_text(text: str, *, today: date | None = None, found: list[str] | None = None) -> str:
    """``text`` with every IBAN, email, ID/Iqama number, mobile number or card number the
    validators recognise replaced by ``[redacted: <rule>]``. Pure; a model-written string
    goes through here before it is saved (ADR 0002). ``found`` collects the rules that hit."""
    day = today or date.today()

    def mark(rule: str) -> str:
        if found is not None:
            found.append(rule)
        return f"[redacted: {rule}]"

    def hit(rules: tuple[str, ...], candidate: str) -> str | None:
        candidate = _strip_invisible(candidate)
        compact = _SEPARATORS.sub("", candidate)
        for rule in rules:
            if validate(rule, candidate, today=day) or validate(rule, compact, today=day):
                return rule
        return None

    def whole(rules: tuple[str, ...]) -> Callable[[re.Match[str]], str]:
        def replace(matched: re.Match[str]) -> str:
            rule = hit(rules, matched.group(0))
            return mark(rule) if rule else matched.group(0)

        return replace

    def numbers(matched: re.Match[str]) -> str:
        """Numbers written with separators, and several in a row ("1000000008 2000000008"):
        try every window of digit groups, longest first."""
        run = matched.group(0)
        groups = list(re.finditer(r"\+?\d+", run))
        out: list[str] = []
        position = i = 0
        while i < len(groups):
            best: tuple[int, str] | None = None
            digits = 0
            for j in range(i, len(groups)):
                digits += len(groups[j].group().lstrip("+"))
                if digits > MAX_NUMBER_DIGITS:
                    break
                if digits >= MIN_NUMBER_DIGITS:
                    candidate = run[groups[i].start() : groups[j].end()]
                    if rule := hit(_NUMBER_RULES, candidate):
                        best = (j, rule)
            if best is None:
                i += 1
                continue
            out.append(run[position : groups[i].start()])
            out.append(mark(best[1]))
            position = groups[best[0]].end()
            i = best[0] + 1
        out.append(run[position:])
        return "".join(out)

    text = _EMAIL_IN_TEXT.sub(whole(("email",)), text)
    text = _IBAN_IN_TEXT.sub(whole(("iban",)), text)
    text = _IPV4_IN_TEXT.sub(whole(("ip_address",)), text)
    text = _IPV6_IN_TEXT.sub(whole(("ip_address",)), text)
    return _DIGITS_IN_TEXT.sub(numbers, text)


def redact_json(value: Any) -> Any:
    """``value`` (JSON-like) with ``redact_text`` applied to every string, keys included."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(k)): redact_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_json(v) for v in value]
    return value
