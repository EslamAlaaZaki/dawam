"""NER-based PII masking of names and addresses in model-written text (#385).

The NER engine is faked at its port (``EntityDetector``) by ``FakeEntityDetector``, which
tags the phrases it is given; one real-engine test (marker ``ner``) runs Presidio with
spaCy (English) and CAMeL Tools (Arabic) on a tiny fixture.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dawam.platform.pii_entities import (
    FakeEntityDetector,
    configure_entity_detector,
    entity_detector,
    set_entity_detector,
)
from dawam.platform.pii_validators import redact_json, redact_text

TODAY = date(2026, 10, 7)
FIXTURES = Path(__file__).parent / "fixtures" / "ner"

FAKE = FakeEntityDetector(
    {
        "Sara Lindqvist": "person",
        "Omar Haddad": "person",
        "Olaya Street": "location",
        "Riyadh": "location",
        "سارة الحربي": "person",
        "عمر الحداد": "person",
        "شارع العليا": "location",
        "العليا": "location",
        "الرياض": "location",
        "جدة": "location",
    }
)


@pytest.fixture
def ner():
    set_entity_detector(FAKE)
    yield FAKE
    set_entity_detector(None)


def test_english_names_are_masked_in_the_existing_style(ner):
    assert redact_text("Ask Sara Lindqvist or Omar Haddad.", today=TODAY) == (
        "Ask [redacted: person_name] or [redacted: person_name]."
    )


def test_arabic_names_are_masked_in_the_existing_style(ner):
    assert redact_text("راجعت سارة الحربي الجدول مع عمر الحداد.", today=TODAY) == (
        "راجعت [redacted: person_name] الجدول مع [redacted: person_name]."
    )


def test_an_english_address_is_masked_whole_with_its_number_and_postal_code(ner):
    text = "Sara Lindqvist lives at 12 Olaya Street, Riyadh 12211."

    assert redact_text(text, today=TODAY) == (
        "[redacted: person_name] lives at [redacted: address]."
    )


def test_an_arabic_address_is_masked_whole(ner):
    text = "يسكن عمر الحداد في شارع العليا، الرياض 12211."

    assert redact_text(text, today=TODAY) == (
        "يسكن [redacted: person_name] في [redacted: address]."
    )


def test_an_arabic_address_keyword_before_the_place_is_masked_with_it(ner):
    assert redact_text("العنوان: حي العليا، الرياض", today=TODAY) == (
        "العنوان: [redacted: address]"
    )


def test_a_place_on_its_own_is_not_an_address(ner):
    for text in ("Sales in Riyadh rose 12% in 2025.", "ارتفعت المبيعات في جدة والرياض."):
        assert redact_text(text, today=TODAY) == text


def test_regex_detection_keeps_working_next_to_names(ner):
    text = "Omar Haddad: a.b@example.com, 0501234567, SA0380000000608010167519."
    found: list[str] = []

    assert redact_text(text, today=TODAY, found=found) == (
        "[redacted: person_name]: [redacted: email], [redacted: phone], [redacted: iban]."
    )
    assert sorted(found) == ["email", "iban", "person_name", "phone"]


def test_an_entity_inside_a_regex_match_does_not_split_it():
    detector = FakeEntityDetector({"sara": "person"})
    set_entity_detector(detector)
    try:
        assert redact_text("mail sara@example.com", today=TODAY) == "mail [redacted: email]"
    finally:
        set_entity_detector(None)


def test_entities_can_be_left_out_and_json_is_masked_too(ner):
    assert redact_text("Omar Haddad", entities=False) == "Omar Haddad"
    assert redact_json({"by": ["Omar Haddad"]}) == {"by": ["[redacted: person_name]"]}


def test_without_a_detector_only_the_regex_rules_run():
    assert entity_detector() is None
    assert redact_text("Omar Haddad, a.b@example.com", today=TODAY) == (
        "Omar Haddad, [redacted: email]"
    )


class _Settings:
    pii_ner_enabled = False
    pii_ner_english_model = "en_core_web_sm"
    pii_ner_arabic_model = "CAMeL-Lab/bert-base-arabic-camelbert-msa-ner"


def test_configure_leaves_ner_off_unless_turned_on():
    set_entity_detector(FAKE)
    try:
        assert configure_entity_detector(_Settings()) is None  # type: ignore[arg-type]
        assert entity_detector() is None
    finally:
        set_entity_detector(None)


@pytest.mark.ner
def test_real_presidio_masks_arabic_and_english_names_and_addresses():
    settings = _Settings()
    settings.pii_ner_enabled = True
    try:
        configure_entity_detector(settings)  # type: ignore[arg-type]
        for case in ("en", "ar"):
            text = (FIXTURES / f"{case}.txt").read_text(encoding="utf-8").strip()
            expected = (FIXTURES / f"{case}.masked.txt").read_text(encoding="utf-8").strip()
            assert redact_text(text, today=TODAY) == expected
    finally:
        set_entity_detector(None)
