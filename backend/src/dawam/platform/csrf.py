"""Double-submit CSRF protection for every state-changing API request (spec §6.1).

- ``CsrfCookieMiddleware`` gives every client that has no CSRF cookie a random token
  in the ``dawam_csrf`` cookie, on whatever response it gets first (the frontend's
  page load or first API call). The cookie is readable by JavaScript on purpose.
- ``require_csrf`` is a dependency on the whole ``/api/v1`` router: a ``POST``,
  ``PUT``, ``PATCH`` or ``DELETE`` must send the same token in the ``X-CSRF-Token``
  header, or it is rejected with ``403 csrf_failed`` before the handler runs. A
  cross-site page can make the browser send the cookie but cannot read it, so it
  cannot copy it into the header.

The session cookie is also ``SameSite=Lax``; this token is the second line of defence.
"""

from __future__ import annotations

import hmac
import re
import secrets

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dawam.platform.errors import ApiError

CSRF_COOKIE = "dawam_csrf"
CSRF_HEADER = "X-CSRF-Token"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_VALID_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _is_valid_token(value: str | None) -> bool:
    return value is not None and _VALID_TOKEN.match(value) is not None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    """Reject a state-changing request whose header token does not match its cookie."""
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    header = request.headers.get(CSRF_HEADER) or ""
    # Both are checked to be ASCII tokens before the constant-time comparison.
    if not (
        _is_valid_token(cookie)
        and _is_valid_token(header)
        and hmac.compare_digest(cookie.encode("ascii"), header.encode("ascii"))
    ):
        raise ApiError(
            403,
            "csrf_failed",
            "The request has no valid CSRF token. Reload the page and try again.",
        )


class CsrfCookieMiddleware:
    """Sets the CSRF cookie on the response to any request that does not carry one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_valid_token(
            HTTPConnection(scope).cookies.get(CSRF_COOKIE)
        ):
            await self.app(scope, receive, send)
            return

        cookie = f"{CSRF_COOKIE}={new_csrf_token()}; Path=/; SameSite=Lax"
        if scope.get("scheme") == "https":
            cookie += "; Secure"

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                already_set = any(
                    value.split("=", 1)[0].strip() == CSRF_COOKIE
                    for value in headers.getlist("set-cookie")
                )
                if not already_set:
                    headers.append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, send_with_cookie)
