"""The security headers of the spec's baseline (§8.4), on every HTTP response.

``SecurityHeadersMiddleware`` is the outermost app middleware, so API JSON, the
served frontend, the probes and every error response (including the last-resort 500
rendered by ``dawam.platform.request_context``) carry:

- ``Content-Security-Policy``: ``APP_CSP``, unless the response already set its own
  (the API docs page does, see ``dawam.platform.api_docs``). Every policy includes
  ``frame-ancestors 'none'``. The built frontend has no inline scripts or styles, so
  ``'self'`` is enough for it.
- ``X-Content-Type-Options: nosniff``.
- ``Referrer-Policy: same-origin``: no referrer ever leaves the site, while same-origin
  requests keep it.
- ``Strict-Transport-Security``, only when the request came over HTTPS and
  ``DAWAM_HSTS_MAX_AGE_SECONDS`` is above 0. Behind a TLS-terminating proxy the scheme
  comes from its ``X-Forwarded-Proto``, which uvicorn trusts only from
  ``DAWAM_FORWARDED_ALLOW_IPS``.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

APP_CSP = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
"""The policy for everything the app serves: its own code only, never framed."""


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts_max_age_seconds: int) -> None:
        self.app = app
        self.hsts = f"max-age={hsts_max_age_seconds}" if hsts_max_age_seconds > 0 else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        over_tls = scope.get("scheme") == "https"

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", APP_CSP)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                if over_tls and self.hsts:
                    headers["Strict-Transport-Security"] = self.hsts
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
