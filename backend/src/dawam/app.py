"""Composition root: builds the FastAPI app and wires implementations to ports.

This is the only place that decides which concrete adapters (email sender, job
runner, ...) the app uses; tests pass their own ``Services``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI

import dawam
from dawam.modules import ALL_MODULES
from dawam.modules.jobs import InlineJobRunner, JobRunner
from dawam.platform import health, meta
from dawam.platform.config import Settings
from dawam.platform.db import create_engine
from dawam.platform.email import EmailSender, LoggingEmailSender
from dawam.platform.errors import ERROR_RESPONSES, install_error_handlers
from dawam.platform.logs import install_request_id_on_records
from dawam.platform.request_context import RequestContextMiddleware

API_PREFIX = "/api/v1"


@dataclass
class Services:
    email: EmailSender
    jobs: JobRunner


def default_services() -> Services:
    # Jobs run inline until the Postgres queue arrives (#41).
    return Services(email=LoggingEmailSender(), jobs=InlineJobRunner())


def create_app(settings: Settings | None = None, *, services: Services | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # read from env
    services = services or default_services()
    install_request_id_on_records()
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="DAWAM",
        version=dawam.__version__,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.services = services
    app.state.engine = engine

    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    api = APIRouter(prefix=API_PREFIX, responses=ERROR_RESPONSES)
    api.include_router(meta.router)
    for module in ALL_MODULES:
        router = getattr(module, "router", None)
        if router is not None:
            api.include_router(router)
    app.include_router(api)
    app.include_router(health.router)
    return app
