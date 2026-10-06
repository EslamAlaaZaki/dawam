"""PII name rules (spec §6.12, stories 130, 133): English, Arabic-transliterated and
Arabic-script keywords matched on normalised column names. Pure unit tests."""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.pii_rules import (
    CATEGORIES,
    NAME_CONFIDENCE_FLOOR,
    match_name,
    normalise,
)


@pytest.mark.parametrize(
    ("name", "rule", "category"),
    [
        # English
        ("national_id", "national_id", "direct_identifier"),
        ("NationalID", "national_id", "direct_identifier"),
        ("email", "email", "direct_identifier"),
        ("customer-email", "email", "direct_identifier"),
        ("iban", "iban", "financial"),
        ("Account Number", "bank_account", "financial"),
        ("birth_date", "birth_date", "quasi_identifier"),
        ("DOB", "birth_date", "quasi_identifier"),
        ("full_name", "person_name", "direct_identifier"),
        ("passport_no", "passport", "direct_identifier"),
        ("home_address", "address", "quasi_identifier"),
        ("religion", "religion", "sensitive"),
        ("blood_type", "health", "sensitive"),
        ("salary", "salary", "financial"),
        # Arabic transliteration
        ("iqama", "iqama", "direct_identifier"),
        ("IQAMA_NO", "iqama", "direct_identifier"),
        ("hawiya", "hawiya", "direct_identifier"),
        ("jawal", "phone", "direct_identifier"),
        ("rqm_jawal", "phone", "direct_identifier"),
        ("jawaz_safar", "passport", "direct_identifier"),
        # Arabic script
        ("رقم_الهوية", "national_id", "direct_identifier"),
        ("رقم الهويه", "national_id", "direct_identifier"),
        ("رقم_الاقامة", "iqama", "direct_identifier"),
        ("رقم_الجوال", "phone", "direct_identifier"),
        ("البريد_الإلكتروني", "email", "direct_identifier"),
        ("الاسم_الكامل", "person_name", "direct_identifier"),
        ("تاريخ_الميلاد", "birth_date", "quasi_identifier"),
        ("الجنسية", "nationality", "quasi_identifier"),
        ("الديانة", "religion", "sensitive"),
        ("رقم_الايبان", "iban", "financial"),
        ("الراتب", "salary", "financial"),
        ("العنوان", "address", "quasi_identifier"),
    ],
)
def test_a_known_name_matches_its_rule(name: str, rule: str, category: str):
    found = match_name(name)

    assert found is not None, name
    assert (found.rule, found.category) == (rule, category)
    assert found.category in CATEGORIES


@pytest.mark.parametrize(
    "name",
    ["cust_no", "branch_code", "amount", "txn_at", "notes", "المدينة", "رقم", "balance", "valid"],
)
def test_an_ordinary_name_does_not_match(name: str):
    assert match_name(name) is None


def test_a_name_match_alone_is_at_least_suggested():
    for name in ("national_id", "gender", "marital_status", "رقم_الهوية", "email"):
        found = match_name(name)
        assert found is not None
        assert found.confidence >= NAME_CONFIDENCE_FLOOR == 0.5


def test_evidence_names_the_column_and_rule():
    found = match_name("رقم_الهوية")

    assert found is not None
    assert "رقم_الهوية" in found.evidence and "national_id" in found.evidence


def test_normalising_drops_separators_case_and_arabic_spelling_variants():
    assert normalise("National_ID") == normalise("national-id") == "nationalid"
    assert normalise("الهُوِيَّة") == normalise("الهوية") == "الهويه"
    assert normalise("إقامة") == normalise("اقامه")
    assert normalise("___") == ""
    assert match_name("___") is None
