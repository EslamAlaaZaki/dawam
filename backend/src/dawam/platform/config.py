"""Settings, read only from environment variables (and an optional ``.env`` file).

Secrets live here too and nowhere else: never in the database or the code. A missing
or malformed required setting stops startup with a ``ConfigError`` that names the
variable and says how to fix it.
"""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from pydantic import (
    Field,
    SecretBytes,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
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
    """The raw key from its base64 text.

    Either alphabet (standard or URL-safe), with or without ``=`` padding, and
    surrounding whitespace is ignored. Raises ``ValueError`` unless it decodes to
    exactly 32 bytes.
    """
    text = value.strip().replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        key = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != ENCRYPTION_KEY_BYTES:
        raise ValueError("not a base64-encoded 32-byte key")
    return key


class DatabaseSettings(BaseSettings):
    """Just what reaching the database needs; Alembic's command line reads only this."""

    model_config = SettingsConfigDict(
        env_prefix="DAWAM_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,  # errors must never carry a secret
    )

    database_url: str
    """SQLAlchemy URL, e.g. ``postgresql+psycopg://dawam:dawam@db:5432/dawam``."""


class Settings(DatabaseSettings):
    encryption_key: SecretBytes
    """The 32-byte AES-256-GCM key for stored credentials (§6.3), decoded from
    ``DAWAM_ENCRYPTION_KEY`` (base64). ``get_secret_value()`` returns the raw bytes."""

    log_level: str = "INFO"
    run_migrations_on_startup: bool = True

    worker_poll_seconds: float = 5.0
    worker_heartbeat_seconds: float = Field(default=10.0, gt=0)
    """How often a worker tells the queue it is still running its job."""
    job_stale_seconds: float = Field(default=60.0, gt=0)
    """A running job whose worker has been silent this long is failed."""

    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)
    """``Strict-Transport-Security`` max-age, sent only on HTTPS requests; 0 turns HSTS off."""

    forwarded_allow_ips: str = "127.0.0.1"
    """Proxies whose ``X-Forwarded-Proto``/``-For`` the app trusts (comma-separated IPs or
    networks, or ``*``): in Compose, the ``edge`` proxy. Only a request from one of them
    can count as HTTPS or name a client address other than its own."""

    @field_validator("encryption_key", mode="before")
    @classmethod
    def _decode_encryption_key(cls, value: object) -> object:
        """Base64 text (from the environment) becomes the raw key; raw bytes must be 32."""
        if isinstance(value, SecretBytes):
            value = value.get_secret_value()
        try:
            if isinstance(value, str):
                return decode_encryption_key(value)
            if isinstance(value, bytes) and len(value) == ENCRYPTION_KEY_BYTES:
                return value
        except ValueError:
            pass
        # The message never includes the value itself.
        raise PydanticCustomError(
            "invalid_encryption_key",
            f"must be {ENCRYPTION_KEY_BYTES} bytes, base64-encoded (43 or 44 characters)",
        )

    admin_email: str | None = None
    admin_password: SecretStr | None = None
    """The first admin (the spec's ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``): created at
    startup only if no admin exists yet. Empty counts as unset. The auth module
    checks them (both set, valid), and only when it is about to create the admin."""

    @field_validator("admin_email", "admin_password", mode="before")
    @classmethod
    def _empty_admin_value_is_unset(cls, value: object) -> object:
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and not text.strip():
            return None
        return value

    session_idle_timeout_hours: float = Field(default=8, gt=0)
    """A session ends after this long without a request."""
    session_absolute_timeout_days: float = Field(default=14, gt=0)
    """A session ends this long after sign-in, however active it is."""

    login_max_failures: int = Field(default=5, ge=1)
    """Consecutive failed sign-ins after which an account locks."""
    login_lockout_minutes: float = Field(default=15, gt=0)
    """How long a locked account stays locked."""
    login_ip_max_failures: int = Field(default=20, ge=1)
    """Failed sign-ins from one client address, within ``login_ip_window_minutes``,
    after which that address must wait before trying again."""
    login_ip_window_minutes: float = Field(default=15, gt=0)

    register_ip_max_attempts: int = Field(default=10, ge=1)
    """Sign-up attempts from one client address, within ``register_ip_window_minutes``,
    after which that address must wait before signing up again."""
    register_ip_window_minutes: float = Field(default=60, gt=0)

    password_reset_cooldown_minutes: float = Field(default=5, gt=0)
    """At most one password reset email per account this often."""
    password_reset_ip_max_requests: int = Field(default=10, ge=1)
    """Password reset emails one client address may cause within
    ``password_reset_ip_window_minutes``; further requests send nothing."""
    password_reset_ip_window_minutes: float = Field(default=60, gt=0)

    storage_backend: Literal["local", "s3"] = "local"
    """Where uploaded files live: a directory (``local``, the default) or an
    S3-compatible bucket (``s3``; needs the ``s3`` extra, ``pip install dawam[s3]``)."""
    storage_path: str = "data/files"
    """The directory of the ``local`` backend: mount a persistent volume here."""
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    """For S3-compatible stores other than AWS (MinIO, Ceph, ...)."""
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    """Left unset, the S3 client falls back to its usual credential chain."""
    upload_max_mb: float = Field(default=25, gt=0)
    """Largest file an upload may carry, in megabytes (MiB)."""
    assistant_max_tool_calls: int = Field(default=25, ge=1)
    """Tool calls the assistant may make while answering one message (spec §6.16)."""
    score_debounce_seconds: float = Field(default=2.0, ge=0)
    """How long after a design change the Data Warehouse score is recalculated, so a burst of
    edits is scored once; 0 scores at once."""

    @property
    def upload_max_bytes(self) -> int:
        return int(self.upload_max_mb * 1024 * 1024)

    public_url: str = "http://localhost:8000"
    """Where users reach DAWAM (scheme, host, port and any path prefix): the start of
    every link DAWAM emails. Configured, never taken from a request's ``Host``, so a
    forged request cannot point a reset link elsewhere."""

    @model_validator(mode="after")
    def _worker_reports_before_a_job_counts_as_lost(self) -> Settings:
        if self.worker_heartbeat_seconds >= self.job_stale_seconds:
            raise PydanticCustomError(
                "heartbeat_not_below_stale",
                "DAWAM_WORKER_HEARTBEAT_SECONDS must be less than DAWAM_JOB_STALE_SECONDS, "
                "or every running job would be failed as lost",
            )
        return self

    @field_validator("public_url")
    @classmethod
    def _public_url_is_an_http_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        scheme, _, rest = value.partition("://")
        if scheme.lower() not in ("http", "https") or not rest or rest.startswith("/"):
            raise PydanticCustomError(
                "invalid_public_url",
                "must be an http:// or https:// URL, e.g. https://dawam.example.com",
            )
        return value


def load_settings() -> Settings:
    """Read the settings from the environment.

    Raises ``ConfigError`` listing every missing or invalid ``DAWAM_*`` variable.
    """
    return _load(Settings)


def load_database_url() -> str:
    """Read only ``DAWAM_DATABASE_URL`` (for Alembic's command line); no secrets needed."""
    return _load(DatabaseSettings).database_url


def _load[T: DatabaseSettings](settings_class: type[T]) -> T:
    try:
        return settings_class()  # type: ignore[call-arg]  # required fields come from the env
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from None


def _describe(exc: ValidationError) -> str:
    lines = ["DAWAM is not configured: fix these environment variables (or your .env file):"]
    for error in exc.errors(include_input=False):
        if not error["loc"]:  # a rule across several settings names them itself
            lines.append(f"  - {error['msg']}.")
            continue
        field = str(error["loc"][0])
        name = f"DAWAM_{field.upper()}"
        problem = "is not set" if error["type"] == "missing" else f"is invalid: {error['msg']}"
        hint = _HINTS.get(field)
        lines.append(f"  - {name} {problem}." + (f" {hint}" if hint else ""))
    return "\n".join(lines)
