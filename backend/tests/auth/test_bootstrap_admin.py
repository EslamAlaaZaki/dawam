"""On first boot DAWAM creates an admin from ``DAWAM_ADMIN_EMAIL`` / ``DAWAM_ADMIN_PASSWORD``
if no admin exists; later boots never create or change one (spec story 13, §6.1).

Invalid values stop startup in the settings (see ``tests/test_config.py``)."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.platform.config import ConfigError, Settings
from dawam.platform.logs import JsonFormatter
from tests.helpers import sign_in

ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "first boot password"


@pytest.fixture
def boot(app, settings: Settings, services):
    """Start (and stop) the app with the given bootstrap admin settings."""

    def boot_with(**admin: str) -> None:
        configured = Settings(**{**settings.model_dump(), **admin})
        with TestClient(create_app(configured, services=services)):
            pass

    return boot_with


def signs_in(client: TestClient, email: str, password: str) -> bool:
    return sign_in(client, email, password).status_code == 200


def test_first_boot_creates_the_admin(boot, anonymous_client):
    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    response = sign_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)

    assert response.status_code == 200
    assert response.json()["email"] == ADMIN_EMAIL
    assert response.json()["system_role"] == "admin"
    assert response.json()["display_name"] == "Administrator"


def test_later_boots_do_not_create_another_admin_or_change_it(boot, anonymous_client):
    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)
    boot(admin_email=ADMIN_EMAIL, admin_password="a changed password")
    boot(admin_email="other-admin@example.com", admin_password="another password")

    assert not signs_in(anonymous_client, ADMIN_EMAIL, "a changed password")
    assert not signs_in(anonymous_client, "other-admin@example.com", "another password")
    assert signs_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)


def test_no_admin_is_created_when_one_already_exists(boot, create_user, anonymous_client):
    existing = create_user(email="existing-admin@example.com", system_role="admin")

    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    assert not signs_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert signs_in(anonymous_client, existing.email, existing.password)


def test_the_admin_email_of_a_non_admin_user_stops_startup_and_leaves_the_user_alone(
    boot, create_user, anonymous_client
):
    regular = create_user(email=ADMIN_EMAIL, password="the users own password")

    with pytest.raises(ConfigError, match="DAWAM_ADMIN_EMAIL"):
        boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    assert not signs_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = sign_in(anonymous_client, ADMIN_EMAIL, regular.password)
    assert response.json()["system_role"] == "user"


def test_without_the_settings_no_admin_is_created(boot, anonymous_client):
    boot()

    assert not signs_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)


def test_the_admin_email_is_not_logged(boot, create_user, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    create_user(email="taken@example.com")
    with pytest.raises(ConfigError):
        boot(admin_email="taken@example.com", admin_password=ADMIN_PASSWORD)
    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    logged = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "bootstrap admin created" in logged
    assert "DAWAM_ADMIN_EMAIL" in logged  # the startup failure is logged, naming the variable
    for secret in (ADMIN_EMAIL, "taken@example.com", ADMIN_PASSWORD):
        assert secret not in logged


@pytest.mark.parametrize(
    "admin", [{"admin_email": ADMIN_EMAIL}, {"admin_password": ADMIN_PASSWORD}]
)
def test_the_email_and_password_must_be_set_together(settings: Settings, admin):
    with pytest.raises(ValidationError, match="DAWAM_ADMIN_EMAIL and DAWAM_ADMIN_PASSWORD"):
        Settings(**{**settings.model_dump(), **admin})


def test_the_admin_password_never_shows_in_the_settings_repr(settings: Settings):
    configured = Settings(
        **{**settings.model_dump(), "admin_email": ADMIN_EMAIL, "admin_password": ADMIN_PASSWORD}
    )

    assert ADMIN_PASSWORD not in repr(configured)
    assert configured.admin_password is not None
    assert configured.admin_password.get_secret_value() == ADMIN_PASSWORD
