"""Running and inspecting Alembic migrations (scripts live in ``dawam/migrations``)."""

from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

SCRIPT_LOCATION = Path(__file__).resolve().parent.parent / "migrations"

_VERSION_TABLE = "alembic_version"

# Serialises concurrent upgrades (e.g. several app replicas starting at once).
_ADVISORY_LOCK_KEY = 0x0DA3A3

logger = logging.getLogger(__name__)


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def head_revisions() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_revisions(engine: sa.Engine) -> set[str]:
    """Revisions applied to the database. Raises ``SQLAlchemyError`` if unreachable."""
    # Plain SQL rather than alembic's MigrationContext, which logs on every call and
    # this runs on every /readyz probe.
    with engine.connect() as conn:
        if not sa.inspect(conn).has_table(_VERSION_TABLE):
            return set()
        return set(conn.scalars(sa.text(f"SELECT version_num FROM {_VERSION_TABLE}")))


def is_at_head(engine: sa.Engine) -> bool:
    return current_revisions(engine) == head_revisions()


def upgrade_to_head(engine: sa.Engine) -> None:
    config = alembic_config()
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    logger.info("database migrated", extra={"revisions": sorted(head_revisions())})
