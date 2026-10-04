"""Encrypting the secrets DAWAM must store (spec §6.3): AES-256-GCM under
``DAWAM_ENCRYPTION_KEY``.

Any module that keeps a credential it must later use in clear (the SMTP password,
Source System connection passwords, LLM API keys, ...) seals it with a ``SecretBox``
built from ``settings.encryption_key`` and stores only the sealed text. Hashes, not
this, are for secrets DAWAM only needs to recognise (passwords, tokens).

A sealed value is ``v1.<base64url(nonce || ciphertext || tag)>``: a fresh random
96-bit nonce per value, and the ``context`` (what the value is, e.g.
``"smtp.password"``) as associated data, so a sealed value copied into another
column does not decrypt there. The ``v1`` prefix leaves room for key rotation.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16
_VERSION = "v1"


class DecryptionError(Exception):
    """The value is malformed, was sealed under another key or context, or was altered."""


class SecretBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"the encryption key must be {KEY_BYTES} bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return f"{_VERSION}.{base64.urlsafe_b64encode(nonce + sealed).decode('ascii')}"

    def decrypt(self, sealed: str, *, context: str) -> str:
        version, _, payload = sealed.partition(".")
        if version != _VERSION or not payload:
            raise DecryptionError("not a sealed value")
        try:
            raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise DecryptionError("not a sealed value") from None
        if len(raw) < _NONCE_BYTES + _TAG_BYTES:
            raise DecryptionError("not a sealed value")
        try:
            plaintext = self._aead.decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode("utf-8")
            )
        except InvalidTag:
            raise DecryptionError("the value does not decrypt with this key and context") from None
        return plaintext.decode("utf-8")
