"""Redaction of model-written text and the strong-rule value check (ADR 0002, spec §6.8)."""

from __future__ import annotations

import pytest

from dawam.platform.pii_validators import first_match, redact_json, redact_text

IBAN = "SA0380000000608010167519"
ARABIC_ID = "".join(chr(0x660 + int(digit)) for digit in "1000000008")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"pay {IBAN} now", "pay [redacted: iban] now"),
        ("pay sa03 8000 0000 6080 1016 7519.", "pay [redacted: iban]."),
        ("mail a.b@leak.example!", "mail [redacted: email]!"),
        ("id 1000000008 ok", "id [redacted: national_id] ok"),
        ("id 1 0 0 0 0 0 0 0 0 8", "id [redacted: national_id]"),
        ("id 1​000000008", "id [redacted: national_id]"),
        (f"id {ARABIC_ID}", "id [redacted: national_id]"),
        ("1000000008 1000000008", "[redacted: national_id] [redacted: national_id]"),
        ("call +966 50 123 4567", "call [redacted: phone]"),
        ("card 4111 1111 1111 1111", "card [redacted: card_number]"),
        ("order 1000000009 of 12 items", "order 1000000009 of 12 items"),
        ("from 192.168.1.20 today", "from [redacted: ip_address] today"),
        ("host 2001:db8::ff00:42:8329 up", "host [redacted: ip_address] up"),
        ("::1 loopback", "[redacted: ip_address] loopback"),
        ("version 1.2.3.4.5 and time 12:30:45", "version 1.2.3.4.5 and time 12:30:45"),
        ("mail محمد@مثال.سعودية now", "mail [redacted: email] now"),
        ("mail jürgen@bücher.example now", "mail [redacted: email] now"),
    ],
)
def test_redact_text(text: str, expected: str):
    assert redact_text(text) == expected


def test_redact_json_walks_values_and_keys_but_keeps_ids():
    uid = "12345678-1234-1234-1234-123456789012"

    assert redact_json({"a": [f"x {IBAN}"], "1000000008": uid}) == {
        "a": ["x [redacted: iban]"],
        "[redacted: national_id]": uid,
    }


def test_first_match_uses_only_strong_rules():
    assert first_match("1000000008") == "national_id"
    assert first_match(ARABIC_ID) == "national_id"
    assert first_match("2026-01-01") is None and first_match("1010123456") is None
