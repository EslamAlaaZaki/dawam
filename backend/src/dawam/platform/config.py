"""Settings, read only from environment variables (and an optional ``.env`` file)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DAWAM_", env_file=".env", extra="ignore")

    database_url: str
    """SQLAlchemy URL, e.g. ``postgresql+psycopg://dawam:dawam@db:5432/dawam``."""

    log_level: str = "INFO"
    run_migrations_on_startup: bool = True
    frontend_dist: Path | None = None
    """Directory with the built frontend; served at ``/`` when set."""

    worker_poll_seconds: float = 5.0


def load_settings() -> Settings:
    """Read the settings from the environment; fails if ``DAWAM_DATABASE_URL`` is unset."""
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
