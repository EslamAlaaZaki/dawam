"""Stored credentials are encrypted with AES-256-GCM under DAWAM_ENCRYPTION_KEY (spec §6.3)."""

import base64
import secrets

import pytest

from dawam.platform.crypto import DecryptionError, SecretBox

KEY = secrets.token_bytes(32)


def test_a_value_round_trips():
    box = SecretBox(KEY)

    sealed = box.encrypt("s3cret pässword", context="smtp.password")

    assert box.decrypt(sealed, context="smtp.password") == "s3cret pässword"


def test_the_ciphertext_does_not_contain_the_value():
    sealed = SecretBox(KEY).encrypt("hunter2-hunter2", context="smtp.password")

    assert "hunter2" not in sealed
    assert b"hunter2" not in base64.urlsafe_b64decode(sealed.split(".", 1)[1] + "==")


def test_every_encryption_uses_a_fresh_nonce():
    box = SecretBox(KEY)

    first = box.encrypt("same value", context="smtp.password")
    second = box.encrypt("same value", context="smtp.password")

    assert first != second
    nonce = lambda sealed: base64.urlsafe_b64decode(sealed.split(".", 1)[1] + "==")[:12]  # noqa: E731
    assert nonce(first) != nonce(second)


def test_the_format_is_versioned():
    assert SecretBox(KEY).encrypt("x", context="c").startswith("v1.")


def test_another_key_cannot_decrypt():
    sealed = SecretBox(KEY).encrypt("value", context="smtp.password")

    with pytest.raises(DecryptionError):
        SecretBox(secrets.token_bytes(32)).decrypt(sealed, context="smtp.password")


def test_a_value_sealed_for_one_purpose_cannot_be_used_for_another():
    box = SecretBox(KEY)
    sealed = box.encrypt("value", context="smtp.password")

    with pytest.raises(DecryptionError):
        box.decrypt(sealed, context="connection.password")


@pytest.mark.parametrize(
    "tamper",
    [
        lambda s: s[:-2] + ("A" if s[-2] != "A" else "B") + s[-1],
        lambda s: "v2" + s[2:],
        lambda s: s.split(".", 1)[1],
        lambda s: "v1.not base64!",
        lambda s: "v1." + base64.urlsafe_b64encode(b"short").decode(),
    ],
)
def test_tampered_or_malformed_values_are_rejected(tamper):
    box = SecretBox(KEY)
    sealed = box.encrypt("value", context="smtp.password")

    with pytest.raises(DecryptionError):
        box.decrypt(tamper(sealed), context="smtp.password")


def test_the_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        SecretBox(secrets.token_bytes(16))
