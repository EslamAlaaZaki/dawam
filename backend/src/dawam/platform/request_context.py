"""Per-request context: the request id, the access log line and the last-resort 500.

``RequestContextMiddleware`` is the outermost app middleware but one:
``SecurityHeadersMiddleware`` wraps it, so even the 500s rendered here carry the
security headers. For every HTTP request it

- takes the caller's ``X-Request-ID`` (if it is a sane token) or generates one,
- makes it available to every log record (see ``dawam.platform.logs``) and returns it
  in the ``X-Request-ID`` response header,
- turns any unhandled exception into the standard ``internal_error`` response, and
- logs one ``request completed`` line with method, path, status and duration.

``client_ip(request)`` is the client address every module records (sessions,
security events, rate limits).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dawam.platform.errors import internal_error_response

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

logger = logging.getLogger("dawam.request")


def current_request_id() -> str | None:
    return request_id_var.get()


def client_ip(request: HTTPConnection) -> str | None:
    """The client's address. uvicorn's proxy-headers middleware (``dawam.app``) sets it
    from ``X-Forwarded-For`` only for ``DAWAM_FORWARDED_ALLOW_IPS``; otherwise it is
    the peer's own address. ``None`` when the server does not know it."""
    return request.client.host if request.client else None


def _incoming_request_id(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", ()):
        if name == wanted:
            candidate = value.decode("latin-1").strip()
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled error")
            if response_started:
                raise
            await internal_error_response()(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)
