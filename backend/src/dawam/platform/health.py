"""Probes outside the versioned API.

- ``/healthz``: the process is up (no dependencies checked).
- ``/readyz``: the database is reachable and its migrations are at head.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from dawam.platform.errors import error_response
from dawam.platform.migrations import is_at_head

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False, response_model=None)
def readyz(request: Request) -> dict[str, object] | JSONResponse:
    try:
        at_head = is_at_head(request.app.state.engine)
    except SQLAlchemyError:
        logger.warning("readiness check: database unreachable", exc_info=True)
        checks = {"database": "unreachable", "migrations": "unknown"}
    else:
        checks = {
            "database": "ok",
            "migrations": "ok" if at_head else "pending",
        }
    if all(value == "ok" for value in checks.values()):
        return {"status": "ok", "checks": checks}
    return error_response(
        503, "not_ready", "DAWAM is not ready to serve requests.", {"checks": checks}
    )
