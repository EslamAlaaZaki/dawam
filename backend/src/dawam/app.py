"""Composition root: builds the FastAPI app and wires implementations to ports.

This is the only place that decides which concrete adapters (email sender, job
runner, ...) the app uses; tests pass their own ``Services``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI

import dawam
from dawam.modules import ALL_MODULES
from dawam.modules.auth import AuthService
from dawam.modules.jobs import InlineJobRunner, JobRunner
from dawam.platform import health, meta
from dawam.platform.api_docs import install_api_docs
from dawam.platform.clock import Clock, system_clock
from dawam.platform.config import ConfigError, Settings, load_settings
from dawam.platform.csrf import CsrfCookieMiddleware, require_csrf
from dawam.platform.db import create_engine
from dawam.platform.email import EmailSender, LoggingEmailSender
from dawam.platform.errors import ERROR_RESPONSES, install_error_handlers
from dawam.platform.logs import install_request_id_on_records
from dawam.platform.migrations import upgrade_to_head
from dawam.platform.request_context import RequestContextMiddleware
from dawam.platform.security_headers import SecurityHeadersMiddleware

API_PREFIX = "/api/v1"

logger = logging.getLogger("dawam.app")


@dataclass
class Services:
    email: EmailSender
    jobs: JobRunner
    clock: Clock = system_clock


def default_services() -> Services:
    # Jobs run inline until the Postgres queue arrives (#41).
    return Services(email=LoggingEmailSender(), jobs=InlineJobRunner())


def create_app(settings: Settings | None = None, *, services: Services | None = None) -> FastAPI:
    settings = settings or load_settings()
    services = services or default_services()
    install_request_id_on_records()
    engine = create_engine(settings.database_url)

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

    install_error_handlers(app)
    app.add_middleware(CsrfCookieMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # Added last, so it is outermost and also covers the 500s RequestContextMiddleware renders.
    app.add_middleware(
        SecurityHeadersMiddleware, hsts_max_age_seconds=settings.hsts_max_age_seconds
    )

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
