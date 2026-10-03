"""The background worker process (``python -m dawam worker``).

It is a composition root like ``dawam.app``. It waits until the app has migrated the
database, then loops until stopped. The Postgres job queue
(``SELECT ... FOR UPDATE SKIP LOCKED``, #41) will be consumed in this loop; until then
the loop only idles. In tests, background work never goes through this process: it
runs inline (see ``dawam.modules.jobs.InlineJobRunner``).
"""

from __future__ import annotations

import logging
import threading

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from dawam.platform.config import Settings
from dawam.platform.db import create_engine
from dawam.platform.migrations import is_at_head

logger = logging.getLogger("dawam.worker")


def _database_ready(engine: sa.Engine) -> bool:
    try:
        return is_at_head(engine)
    except SQLAlchemyError:
        return False


def run_worker(settings: Settings, *, stop: threading.Event) -> None:
    """Run until ``stop`` is set."""
    engine = create_engine(settings.database_url)
    logger.info("worker started")
    try:
        if not stop.is_set() and not _database_ready(engine):
            logger.info("waiting for database migrations")
            while not stop.wait(settings.worker_poll_seconds):
                if _database_ready(engine):
                    break
        if stop.is_set():
            return
        logger.info("worker ready")
        while not stop.wait(settings.worker_poll_seconds):
            pass  # The job queue (#41) is polled here.
    finally:
        engine.dispose()
        logger.info("worker stopped")
