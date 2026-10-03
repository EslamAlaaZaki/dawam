"""Alembic environment for DAWAM's one PostgreSQL database.

Migrations run on app startup (``dawam.platform.migrations.upgrade_to_head``), which
passes its own connection in ``config.attributes["connection"]``. From the command
line (``alembic upgrade head``, ``alembic revision --autogenerate -m ...``, run in
``backend/``) the URL is ``sqlalchemy.url`` if set, else ``DAWAM_DATABASE_URL``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context

import dawam.modules  # noqa: F401  (importing the modules registers their tables)
from dawam.platform.config import Settings
from dawam.platform.db import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url  # type: ignore[call-arg]


def _run(connection: sa.Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = sa.create_engine(_database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
