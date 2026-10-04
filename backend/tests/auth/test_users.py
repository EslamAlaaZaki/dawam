"""Creating and reading users through the auth module's service interface."""

import uuid

import pytest

from dawam.modules.auth import AuthService
from dawam.platform.errors import ApiError


def test_a_taken_email_is_a_409_even_in_another_case(auth_service: AuthService, create_user):
    create_user(email="grace@example.com")

    # There is no pre-check: the second insert itself hits the unique constraint, as a
    # concurrent create would.
    with pytest.raises(ApiError) as raised:
        auth_service.create_user(
            email="Grace@Example.com", password="another password", display_name="Grace"
        )

    assert (raised.value.status_code, raised.value.code) == (409, "email_taken")


@pytest.mark.parametrize(
    ("email", "password", "code"),
    [
        ("not-an-email", "correct horse battery", "invalid_email"),
        ("grace@example.com", "too short", "invalid_password"),
    ],
)
def test_invalid_users_are_rejected(auth_service: AuthService, email, password, code):
    with pytest.raises(ApiError) as raised:
        auth_service.create_user(email=email, password=password, display_name="Grace")

    assert (raised.value.status_code, raised.value.code) == (422, code)


def test_get_user_of_an_unknown_id_is_none(auth_service: AuthService):
    assert auth_service.get_user(uuid.uuid4()) is None
