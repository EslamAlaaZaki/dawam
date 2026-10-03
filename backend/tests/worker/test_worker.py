"""The worker process: waits for the app to migrate the database, then runs until stopped."""

import logging
import threading
import time

import pytest

from dawam.platform.config import Settings
from dawam.platform.db import create_engine
from dawam.platform.migrations import upgrade_to_head
from dawam.worker import run_worker


def wait_for(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.02)


def logged(caplog: pytest.LogCaptureFixture, message: str) -> bool:
    return any(r.getMessage() == message for r in caplog.records)


def test_worker_waits_for_migrations_then_runs_until_stopped(fresh_database_url, caplog):
    caplog.set_level(logging.INFO)
    settings = Settings(database_url=fresh_database_url, worker_poll_seconds=0.05)
    stop = threading.Event()
    worker = threading.Thread(target=run_worker, args=(settings,), kwargs={"stop": stop})
    worker.start()
    try:
        wait_for(lambda: logged(caplog, "waiting for database migrations"))
        assert not logged(caplog, "worker ready")

        engine = create_engine(fresh_database_url)
        upgrade_to_head(engine)
        engine.dispose()

        wait_for(lambda: logged(caplog, "worker ready"))
        assert worker.is_alive()
    finally:
        stop.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert logged(caplog, "worker stopped")


def test_worker_returns_immediately_when_already_stopped(database_url):
    stop = threading.Event()
    stop.set()

    run_worker(Settings(database_url=database_url), stop=stop)
