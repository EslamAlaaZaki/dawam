"""The password policy: at least 10 characters and not a common password (spec §6.1).

It applies wherever a password is set: creating a user (here), the bootstrap admin
(``test_bootstrap_admin.py``), sign-up (``test_registration.py``) and changing a
password (``test_profile.py``).
"""

import pytest

from dawam.modules.auth import AuthService
from dawam.platform.errors import ApiError


def create(auth_service: AuthService, password: str):
    return auth_service.create_user(
        email="grace@example.com", password=password, display_name="Grace Hopper"
    )


@pytest.mark.parametrize(
    "password",
    ["qwertyuiop", "password123", "1q2w3e4r5t", "basketball", "Password123", "QWERTYUIOP"],
)
def test_a_common_password_is_rejected_whatever_its_case(auth_service: AuthService, password):
    with pytest.raises(ApiError) as raised:
        create(auth_service, password)

    assert (raised.value.status_code, raised.value.code) == (422, "invalid_password")
    assert "common" in raised.value.message


def test_a_short_password_is_rejected_for_its_length(auth_service: AuthService):
    with pytest.raises(ApiError) as raised:
        create(auth_service, "123456789")

    assert raised.value.code == "invalid_password"
    assert "at least 10 characters" in raised.value.message


@pytest.mark.parametrize("password", ["correct horse battery", "x" * 10, "Tr0ub4dor&3xyz"])
def test_a_long_enough_uncommon_password_is_accepted(auth_service: AuthService, password):
    user = create(auth_service, password)

    assert user.email == "grace@example.com"
