"""Composition root: builds the FastAPI app and wires implementations to ports.

This is the only place that decides which concrete adapters (email sender, job
runner, ...) the app uses; tests pass their own ``Services``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import dawam
from dawam import job_handlers
from dawam.modules import ALL_MODULES
from dawam.modules.admin import SystemSettingsService
from dawam.modules.auth import AuthService
from dawam.modules.jobs import JobRunner, JobService, QueuedJobRunner
from dawam.modules.mail import MailService
from dawam.modules.workspaces import InvitedWorkspaceMembership
from dawam.platform import health, meta
from dawam.platform.api_docs import install_api_docs
from dawam.platform.body_limit import BodySizeLimitMiddleware
from dawam.platform.clock import Clock, system_clock
from dawam.platform.config import ConfigError, Settings, load_settings
from dawam.platform.csrf import CsrfCookieMiddleware, require_csrf
from dawam.platform.db import create_engine
from dawam.platform.email import EmailSender, SmtpEmailSender
from dawam.platform.errors import ERROR_RESPONSES, install_error_handlers
from dawam.platform.logs import install_request_id_on_records
from dawam.platform.migrations import upgrade_to_head
from dawam.platform.request_context import RequestContextMiddleware
from dawam.platform.security_headers import SecurityHeadersMiddleware
from dawam.platform.storage import create_storage

API_PREFIX = "/api/v1"

UPLOAD_PATH = re.compile(rf"{API_PREFIX}/workspaces/[^/]+/systems/[^/]+/files")
"""The file upload route (files module); its body is size-limited while it streams."""
UPLOAD_OVERHEAD_BYTES = 64 * 1024
"""Allowed on top of the file limit for the multipart framing around the file."""

logger = logging.getLogger("dawam.app")


@dataclass
class Services:
    email: EmailSender
    jobs: JobRunner
    clock: Clock = system_clock


def default_services() -> Services:
    # Jobs are queued in Postgres for the worker process; tests run them inline.
    return Services(email=SmtpEmailSender(), jobs=QueuedJobRunner())


def create_app(settings: Settings | None = None, *, services: Services | None = None) -> FastAPI:
    settings = settings or load_settings()
    services = services or default_services()
    install_request_id_on_records()
    engine = create_engine(settings.database_url)
    # The worker registers the same handlers: a job is submitted here and run there.
    job_handlers.register_job_handlers(
        services.jobs, engine=engine, settings=settings, clock=services.clock
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.run_migrations_on_startup:
            upgrade_to_head(engine)
        try:
            AuthService(engine, settings, clock=services.clock).ensure_bootstrap_admin()
        except ConfigError as exc:
            logger.error(
                "startup failed: DAWAM is not configured correctly", extra={"problem": str(exc)}
            )
            raise
        yield
        engine.dispose()

    app = FastAPI(
        title="DAWAM",
        version=dawam.__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,  # served by install_api_docs, under its own CSP
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.services = services
    app.state.engine = engine
    # Where uploaded files live (local volume or S3-compatible bucket).
    app.state.storage = create_storage(settings)
    # Sign-up (auth) asks the admin module's system settings who may register.
    app.state.registration_policy = SystemSettingsService(engine)
    # The one email-delivery service; auth reaches it here, as its Mailer port.
    app.state.mailer = MailService(engine, settings, sender=services.email, clock=services.clock)
    # Accepting an invitation into a Workspace (auth) joins it through the workspaces module.
    app.state.invited_membership = InvitedWorkspaceMembership(clock=services.clock)

    # Archiving a Workspace (workspaces) cancels its jobs: workspaces cannot import jobs.
    app.state.on_workspace_archived = JobService(
        engine, runner=services.jobs, clock=services.clock
    ).cancel_for_workspace

    install_error_handlers(app)
    app.add_middleware(CsrfCookieMiddleware)
    # Uploads are cut off while they stream, not after Starlette has spooled them all.
    app.add_middleware(
        BodySizeLimitMiddleware,
        path=UPLOAD_PATH,
        limit=lambda: settings.upload_max_bytes + UPLOAD_OVERHEAD_BYTES,
    )
    app.add_middleware(RequestContextMiddleware)
    # Added after the others, so it is outside them and also covers the 500s
    # RequestContextMiddleware renders.
    app.add_middleware(
        SecurityHeadersMiddleware, hsts_max_age_seconds=settings.hsts_max_age_seconds
    )
    # Outermost: everything inside sees the real client address and scheme. Only a
    # request from DAWAM_FORWARDED_ALLOW_IPS (in Compose, the `edge` proxy) may set
    # them through X-Forwarded-For / X-Forwarded-Proto; anyone else's are ignored.
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.forwarded_allow_ips)

    # Every state-changing API request needs the double-submit CSRF token.
    api = APIRouter(
        prefix=API_PREFIX, responses=ERROR_RESPONSES, dependencies=[Depends(require_csrf)]
    )
    api.include_router(meta.router)
    for module in ALL_MODULES:
        router = getattr(module, "router", None)
        if router is not None:
            api.include_router(router)
    app.include_router(api)
    app.include_router(health.router)
    install_api_docs(app, f"{API_PREFIX}/docs")
    return app
