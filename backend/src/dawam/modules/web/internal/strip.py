"""Value stripping: what of a model-written search query may leave the network.

A query should carry concepts (a KPI, a pattern, a vendor's schema), never data. Quoted
literals (how sample values and codes appear in SQL and in chat), long numbers and tokens
carrying many digits (ids, accounts, amounts), dates, and whatever the PII validators
recognise (emails, phones, IBANs, ID and card numbers, and names when NER is on) are
removed. Short numbers stay: "IFRS 9", "SQL Server 2019", "ISO 20022".
"""

from __future__ import annotations

import re

from dawam.platform.pii_validators import redact_text

MAX_QUERY_CHARS = 300

# Straight, backtick and curly quotes (the curly ones written as escapes).
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`|\u201c[^\u201d]*\u201d|\u2018[^\u2019]*\u2019")
_DATE = re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b")
_AMOUNT = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d{2,}\b")
_REDACTED = re.compile(r"\[redacted: [^\]]*\]")
MAX_DIGITS = 5
"""A token with more digits than this is taken as a value (an id, an account, an amount)."""


def _keep(token: str) -> bool:
    return sum(c.isdigit() for c in token) <= MAX_DIGITS


def strip_values(query: str) -> str:
    """``query`` without the values it may carry, whitespace collapsed."""
    text = _QUOTED.sub(" ", query)
    text = _REDACTED.sub(" ", redact_text(text))
    text = _DATE.sub(" ", text)
    text = _AMOUNT.sub(" ", text)
    kept = [token for token in text.split() if _keep(token)]
    return " ".join(kept)[:MAX_QUERY_CHARS].strip()
