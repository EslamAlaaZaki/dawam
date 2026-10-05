"""The background worker process (``python -m dawam worker``).

It is a composition root like ``dawam.app``. It waits until the app has migrated the
database, then loops until stopped: each pass fails the running jobs whose worker died,
then claims queued jobs one at a time (``SELECT ... FOR UPDATE SKIP LOCKED``) and runs
them, telling the queue it is alive while a job runs. In tests, background work never
goes through this process: it runs inline (see ``dawam.modules.jobs.InlineJobRunner``).
Job handlers come from ``dawam.job_handlers``, the registry it shares with the app.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from dawam import job_handlers
from dawam.modules.jobs import Job, JobService, QueuedJobRunner
from dawam.platform.clock import system_clock
from dawam.platform.config import Settings
from dawam.platform.db import create_engine
from dawam.platform.migrations import is_at_head

logger = logging.getLogger("dawam.worker")


def _database_ready(engine: sa.Engine) -> bool:
    try:
        return is_at_head(engine)
    except SQLAlchemyError:
        return False


def _run_job(jobs: JobService, job: Job, settings: Settings) -> None:
    """Run ``job`` in a thread, reporting that this worker is alive while it runs."""
    thread = threading.Thread(target=jobs.execute, args=(job,), name=f"job-{job.id}")
    thread.start()
    while thread.is_alive():
        thread.join(settings.worker_heartbeat_seconds)
        if thread.is_alive():
            # A failed heartbeat must not leave the job running unattended while this
            # worker claims more: keep waiting for it, and report again next time.
            try:
                jobs.heartbeat(job.id)
            except SQLAlchemyError:
                logger.exception("job heartbeat failed", extra={"job_id": str(job.id)})


def run_pending_jobs(jobs: JobService, settings: Settings, *, stop: threading.Event) -> int:
    """One pass: fail jobs whose worker died, then run queued jobs until none is left
    (or ``stop``). Returns how many it ran."""
    jobs.fail_lost()
    ran = 0
    while not stop.is_set() and (job := jobs.claim_next()) is not None:
        _run_job(jobs, job, settings)
        ran += 1
    return ran


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
        runner = QueuedJobRunner()
        job_handlers.register_job_handlers(
            runner, engine=engine, settings=settings, clock=system_clock
        )
        jobs = JobService(
            engine,
            runner=runner,
            clock=system_clock,
            stale_after=timedelta(seconds=settings.job_stale_seconds),
        )
        logger.info("worker ready")
        while not stop.is_set():
            try:
                run_pending_jobs(jobs, settings, stop=stop)
            except SQLAlchemyError:
                logger.exception("job queue unavailable; retrying")
            stop.wait(settings.worker_poll_seconds)
    finally:
        engine.dispose()
        logger.info("worker stopped")
