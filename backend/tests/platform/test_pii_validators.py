"""PII value validators (spec §6.12, story 131): pure unit tests with valid and invalid
Saudi IDs, Iqamas, IBANs, mobiles and cards."""

from __future__ import annotations

from datetime import date

import pytest

from dawam.platform.pii_validators import VALIDATORS, match_ratio, validate

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize("value", ["1000000008", "1234567897", "1087654321"])
def test_valid_national_ids(value: str):
    assert validate("national_id", value)


@pytest.mark.parametrize(
    "value",
    ["1000000009", "2000000006", "100000000", "10000000080", "abcdefghij", "", "1000 000008x"],
)
def test_invalid_national_ids(value: str):
    assert not validate("national_id", value)


def test_national_ids_may_arrive_as_integers():
    assert validate("national_id", 1000000008)


@pytest.mark.parametrize("value", ["2000000006", "2123456788"])
def test_valid_iqamas(value: str):
    assert validate("iqama", value)


@pytest.mark.parametrize("value", ["2000000007", "1000000008", "200000000", "30000000"])
def test_invalid_iqamas(value: str):
    assert not validate("iqama", value)


@pytest.mark.parametrize(
    "value", ["0512345678", "+966512345678", "966512345678", "00966512345678", "05 1234 5678"]
)
def test_valid_mobiles(value: str):
    assert validate("phone", value)


@pytest.mark.parametrize(
    "value",
    ["0412345678", "051234567", "05123456789", "+966412345678", "hello", "+9665123", "055512345"],
)
def test_invalid_mobiles(value: str):
    assert not validate("phone", value)


@pytest.mark.parametrize("value", ["SA0380000000608010167519", "sa03 8000 0000 6080 1016 7519"])
def test_valid_ibans(value: str):
    assert validate("iban", value)


@pytest.mark.parametrize(
    "value",
    [
        "SA0380000000608010167518",  # wrong check digits
        "GB82WEST12345698765432",  # not Saudi
        "SA03800000006080101675",  # too short
        "SA03800000006080101675190",  # too long
    ],
)
def test_invalid_ibans(value: str):
    assert not validate("iban", value)


@pytest.mark.parametrize("value", ["a@b.co", "first.last+tag@example.sa"])
def test_valid_emails(value: str):
    assert validate("email", value)


@pytest.mark.parametrize("value", ["no-at-sign", "a@b", "@x.com", "a b@x.com", "a@@x.com"])
def test_invalid_emails(value: str):
    assert not validate("email", value)


@pytest.mark.parametrize(
    "value", ["4111111111111111", "4111 1111 1111 1111", "5500-0000-0000-0004", "378282246310005"]
)
def test_valid_cards(value: str):
    assert validate("card_number", value)


@pytest.mark.parametrize(
    "value", ["4111111111111112", "0000000000000000", "1234", "41111111111111111111"]
)
def test_invalid_cards(value: str):
    assert not validate("card_number", value)


@pytest.mark.parametrize("value", ["1010123456", "4030987654", "2050111222"])
def test_valid_commercial_registrations(value: str):
    assert validate("commercial_registration", value)


@pytest.mark.parametrize("value", ["9010123456", "101012345", "10101234567", "10101x3456"])
def test_invalid_commercial_registrations(value: str):
    assert not validate("commercial_registration", value)


@pytest.mark.parametrize(
    "value", [date(1990, 5, 1), "1985-12-31", "1990-05-01T00:00:00", date(2020, 1, 1)]
)
def test_valid_dates_of_birth(value):
    assert validate("birth_date", value, today=TODAY)


@pytest.mark.parametrize(
    "value", [date(2027, 1, 1), date(1800, 1, 1), "1850-01-01", "not a date", 42]
)
def test_invalid_dates_of_birth(value):
    assert not validate("birth_date", value, today=TODAY)


@pytest.mark.parametrize("value", ["192.168.1.1", "10.0.0.255", "::1", "2001:db8::ff00:42:8329"])
def test_valid_ip_addresses(value: str):
    assert validate("ip_address", value)


@pytest.mark.parametrize("value", ["256.1.1.1", "1.2.3", "gggg::1", "hello"])
def test_invalid_ip_addresses(value: str):
    assert not validate("ip_address", value)


def test_odd_values_never_match_and_never_raise():
    for rule in VALIDATORS:
        for value in (None, b"\x00\x01", object(), 3.5, True, "x" * 10_000):
            assert validate(rule, value) is False


def test_the_match_ratio_counts_only_non_null_values():
    values = ["1000000008", None, "1000000009", "2000000006", None]

    ratio = match_ratio("national_id", values)

    assert ratio is not None and (ratio.matched, ratio.total) == (1, 3)
    assert match_ratio("national_id", [None, None]) is None


def test_every_rule_has_a_category_and_a_weight():
    for rule, validator in VALIDATORS.items():
        assert validator.id == rule
        assert validator.category in {"direct_identifier", "quasi_identifier", "financial"}
        assert 0 < validator.weight <= 1
