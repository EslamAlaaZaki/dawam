"""On first boot DAWAM creates an admin from ``DAWAM_ADMIN_EMAIL`` / ``DAWAM_ADMIN_PASSWORD``
if no admin exists; later boots never create or change one (spec story 13, §6.1)."""

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dawam.app import create_app
from dawam.platform.config import Settings
from dawam.platform.errors import ApiError
from tests.helpers import sign_in

ADMIN_EMAIL = "root@example.com"
ADMIN_PASSWORD = "first boot password"


@pytest.fixture
def boot(app: FastAPI, settings: Settings, services):
    """Start (and stop) the app with the given bootstrap admin settings."""

    def boot_with(**admin: str) -> None:
        configured = Settings(**{**settings.model_dump(), **admin})
        with TestClient(create_app(configured, services=services)):
            pass

    return boot_with


def user_count(app: FastAPI) -> int:
    with app.state.engine.connect() as conn:
        return conn.scalar(sa.text("SELECT count(*) FROM users"))


def test_first_boot_creates_the_admin(app: FastAPI, boot, anonymous_client):
    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    response = sign_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD)

    assert response.status_code == 200
    assert response.json()["email"] == ADMIN_EMAIL
    assert response.json()["system_role"] == "admin"
    assert anonymous_client.get("/api/v1/me").json()["system_role"] == "admin"
    assert user_count(app) == 1


def test_later_boots_do_not_create_another_admin_or_change_it(app: FastAPI, boot, anonymous_client):
    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)
    boot(admin_email=ADMIN_EMAIL, admin_password="a changed password")
    boot(admin_email="other-admin@example.com", admin_password="another password")

    assert user_count(app) == 1
    assert sign_in(anonymous_client, ADMIN_EMAIL, "a changed password").status_code == 401
    assert (
        sign_in(anonymous_client, "other-admin@example.com", "another password").status_code == 401
    )
    assert sign_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD).status_code == 200


def test_no_admin_is_created_when_one_already_exists(app: FastAPI, boot, create_user):
    create_user(email="existing-admin@example.com", system_role="admin")

    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    assert user_count(app) == 1


def test_an_existing_user_with_the_admin_email_is_left_alone(
    app: FastAPI, boot, create_user, anonymous_client
):
    regular = create_user(email=ADMIN_EMAIL, password="the users own password")

    boot(admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD)

    assert user_count(app) == 1
    assert sign_in(anonymous_client, ADMIN_EMAIL, ADMIN_PASSWORD).status_code == 401
    response = sign_in(anonymous_client, ADMIN_EMAIL, regular.password)
    assert response.json()["system_role"] == "user"


def test_without_the_settings_no_admin_is_created(app: FastAPI, boot):
    boot()

    assert user_count(app) == 0


@pytest.mark.parametrize(
    "admin", [{"admin_email": ADMIN_EMAIL}, {"admin_password": ADMIN_PASSWORD}]
)
def test_the_email_and_password_must_be_set_together(settings: Settings, admin):
    with pytest.raises(ValidationError, match="DAWAM_ADMIN_EMAIL and DAWAM_ADMIN_PASSWORD"):
        Settings(**{**settings.model_dump(), **admin})


@pytest.mark.parametrize(
    ("email", "password", "code"),
    [
        (ADMIN_EMAIL, "too short", "password_too_short"),
        ("not-an-email", ADMIN_PASSWORD, "invalid_email"),
    ],
)
def test_an_invalid_admin_stops_startup_and_creates_nothing(
    app: FastAPI, boot, email, password, code
):
    with pytest.raises(ApiError) as failure:
        boot(admin_email=email, admin_password=password)

    assert failure.value.code == code
    assert user_count(app) == 0


def test_the_admin_password_never_shows_in_the_settings_repr(settings: Settings):
    configured = Settings(
        **{**settings.model_dump(), "admin_email": ADMIN_EMAIL, "admin_password": ADMIN_PASSWORD}
    )

    assert ADMIN_PASSWORD not in repr(configured)
    assert configured.admin_password is not None
    assert configured.admin_password.get_secret_value() == ADMIN_PASSWORD
