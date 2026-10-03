"""Settings, read only from environment variables (and an optional ``.env`` file).

Secrets live here too and nowhere else: never in the database or the code. A missing
or malformed required setting stops startup with a ``ConfigError`` that names the
variable and says how to fix it.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENCRYPTION_KEY_BYTES = 32
GENERATE_ENCRYPTION_KEY = (
    'python -c "import base64, secrets; '
    'print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
)

_HINTS = {
    "database_url": "For example: postgresql+psycopg://dawam:dawam@localhost:5432/dawam",
    "encryption_key": (
        "It encrypts the credentials DAWAM stores, which cannot be read without it. "
        f"Generate one with: {GENERATE_ENCRYPTION_KEY} (or: openssl rand -base64 32)"
    ),
}


class ConfigError(Exception):
    """The environment does not hold a valid configuration; the message says what to fix."""


def decode_encryption_key(value: str) -> bytes:
    """The raw key from its base64 text (standard or URL-safe alphabet, padded)."""
    text = value.strip().replace("-", "+").replace("_", "/")
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != ENCRYPTION_KEY_BYTES:
        raise ValueError("not a base64-encoded 32-byte key")
    return key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DAWAM_", env_file=".env", extra="ignore")

    database_url: str
    """SQLAlchemy URL, e.g. ``postgresql+psycopg://dawam:dawam@db:5432/dawam``."""

    encryption_key: SecretStr
    """32 random bytes, base64-encoded: the AES-256-GCM key for stored credentials (§6.3)."""

    log_level: str = "INFO"
    run_migrations_on_startup: bool = True
    frontend_dist: Path | None = None
    """Directory with the built frontend; served at ``/`` when set."""

    worker_poll_seconds: float = 5.0

    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)
    """``Strict-Transport-Security`` max-age, sent only on HTTPS requests; 0 turns HSTS off."""

    forwarded_allow_ips: str = "127.0.0.1"
    """Proxies whose ``X-Forwarded-Proto``/``-For`` uvicorn trusts (comma-separated IPs or
    networks, or ``*``). Only then does a request a TLS proxy forwards count as HTTPS."""

    @field_validator("encryption_key")
    @classmethod
    def _check_encryption_key(cls, value: SecretStr) -> SecretStr:
        try:
            decode_encryption_key(value.get_secret_value())
        except ValueError:
            # Never put the value itself into the error.
            raise PydanticCustomError(
                "invalid_encryption_key",
                f"must be {ENCRYPTION_KEY_BYTES} bytes, base64-encoded (44 characters)",
            ) from None
        return value


def load_settings() -> Settings:
    """Read the settings from the environment.

    Raises ``ConfigError`` listing every missing or invalid ``DAWAM_*`` variable.
    """
    try:
        return Settings()  # type: ignore[call-arg]  # required fields come from the environment
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from None


def _describe(exc: ValidationError) -> str:
    lines = ["DAWAM cannot start: fix these environment variables (or your .env file):"]
    for error in exc.errors(include_input=False):
        field = str(error["loc"][0]) if error["loc"] else ""
        name = f"DAWAM_{field.upper()}"
        problem = "is not set" if error["type"] == "missing" else f"is invalid: {error['msg']}"
        hint = _HINTS.get(field)
        lines.append(f"  - {name} {problem}." + (f" {hint}" if hint else ""))
    return "\n".join(lines)
