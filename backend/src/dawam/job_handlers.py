"""Every module's background-job handlers, by job type.

A composition root (README rule 4), shared by the two that run jobs: ``dawam.app``
(which needs the handlers to accept a submitted job, and runs them inline in tests) and
``dawam.worker`` (which runs queued jobs). Each ticket that adds a job type (spec §7:
``extract``, ``profile``, ``export``, ...) adds its handler here.
"""

from __future__ import annotations

from collections.abc import Mapping

from dawam.modules.jobs import JobHandler, JobRunner

JOB_HANDLERS: Mapping[str, JobHandler] = {}


def register_job_handlers(runner: JobRunner) -> None:
    """Register every handler in ``JOB_HANDLERS`` on ``runner``."""
    for job_type, handler in JOB_HANDLERS.items():
        runner.register(job_type, handler)
