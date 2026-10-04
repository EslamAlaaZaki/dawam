"""Admins turn self-registration on or off and limit it to allowed email domains
(spec story 20; ``GET|PUT /admin/settings``). Only admins can read or change them."""

import pytest
from fastapi.testclient import TestClient

from dawam.platform.csrf import CSRF_HEADER
from tests.helpers import csrf_token

SETTINGS = "/api/v1/admin/settings"


def put_registration(client: TestClient, enabled: bool, domains: list[str]):
    return client.put(
        SETTINGS,
        json={"registration": {"enabled": enabled, "allowed_email_domains": domains}},
    )


def test_registration_is_off_and_unrestricted_until_an_admin_changes_it(admin_client):
    response = admin_client.get(SETTINGS)

    assert response.status_code == 200
    assert response.json() == {"registration": {"enabled": False, "allowed_email_domains": []}}


def test_an_admin_turns_registration_on_for_some_domains(admin_client):
    response = put_registration(
        admin_client, True, [" Example.COM ", "@acme.org", "example.com", "xn--mgbh0fb.example"]
    )

    expected = {
        "registration": {
            "enabled": True,
            "allowed_email_domains": ["example.com", "acme.org", "xn--mgbh0fb.example"],
        }
    }
    assert response.status_code == 200
    assert response.json() == expected
    assert admin_client.get(SETTINGS).json() == expected


def test_an_admin_turns_registration_off_again(admin_client):
    put_registration(admin_client, True, ["example.com"])

    response = put_registration(admin_client, False, [])

    assert response.json() == {"registration": {"enabled": False, "allowed_email_domains": []}}
    assert admin_client.get(SETTINGS).json() == response.json()


def test_a_put_without_the_registration_section_leaves_it_alone(admin_client):
    put_registration(admin_client, True, ["example.com"])

    response = admin_client.put(SETTINGS, json={})

    assert response.status_code == 200
    assert response.json()["registration"] == {
        "enabled": True,
        "allowed_email_domains": ["example.com"],
    }


@pytest.mark.parametrize(
    "domain", ["", "not a domain", "example", "ex_ample.com", "-example.com", "a@b.com", "é.com"]
)
def test_an_invalid_domain_is_rejected_and_nothing_changes(admin_client, domain):
    response = put_registration(admin_client, True, ["example.com", domain])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_email_domain"
    assert admin_client.get(SETTINGS).json()["registration"]["enabled"] is False


def test_too_many_domains_are_rejected(admin_client):
    response = put_registration(admin_client, True, [f"d{i}.example.com" for i in range(201)])

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_regular_users_can_neither_read_nor_change_the_settings(
    signed_in_client, admin_client, method
):
    response = signed_in_client.request(
        method, SETTINGS, json={"registration": {"enabled": True, "allowed_email_domains": []}}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert admin_client.get(SETTINGS).json()["registration"]["enabled"] is False


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_anonymous_visitors_can_neither_read_nor_change_the_settings(anonymous_client, method):
    response = anonymous_client.request(
        method,
        SETTINGS,
        json={"registration": {"enabled": True, "allowed_email_domains": []}},
        headers={CSRF_HEADER: csrf_token(anonymous_client)},
    )

    assert response.status_code == 401
