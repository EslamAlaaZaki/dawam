"""Redacting values in model-written text with the PII validators (spec §6.8, §6.13)."""

from __future__ import annotations

from datetime import date

from dawam.platform.pii_validators import redact_text

TODAY = date(2026, 10, 7)


def test_redact_text_replaces_distinctive_values_with_their_rule():
    text = "Paid to SA0380000000608010167519, mail a.b@example.com or call 0501234567."

    assert redact_text(text, today=TODAY) == (
        "Paid to [redacted: iban], mail [redacted: email] or call [redacted: phone]."
    )


def test_redact_text_finds_values_spread_over_several_words():
    assert redact_text("IBAN sa03 8000 0000 6080 1016 7519 ok", today=TODAY) == (
        "IBAN [redacted: iban] ok"
    )


def test_redact_text_keeps_ordinary_prose_numbers_and_weak_matches():
    text = "Margin rose 12.5% since 2024-01-01 across 1234567890 accounts."

    assert redact_text(text, today=TODAY) == text


def test_redact_text_keeps_whitespace_and_empty_text():
    assert redact_text("", today=TODAY) == ""
    assert redact_text("a  b\n c", today=TODAY) == "a  b\n c"
