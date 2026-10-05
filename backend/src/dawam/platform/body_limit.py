"""A request-body size guard for upload routes.

Starlette spools a whole multipart body to disk before a handler runs, so a limit
checked in the handler comes too late. ``BodySizeLimitMiddleware`` enforces it while the
body streams: a declared ``Content-Length`` over the limit is refused at once, and for
chunked or lying requests the bytes are counted as they arrive; past the limit the
request is cut off and answered ``413 file_too_large`` whatever the app would have said.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_response


class BodySizeLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, path: re.Pattern[str], limit: Callable[[], int], methods=("POST",)
    ) -> None:
        self.app = app
        self.path = path
        self.limit = limit
        self.methods = methods

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in self.methods
            or not self.path.fullmatch(scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        limit = self.limit()
        too_large = error_response(
            413,
            "file_too_large",
            f"The upload is larger than the {limit // (1024 * 1024)} MB limit.",
            {"max_bytes": limit},
        )
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await too_large(scope, receive, send)
            return

        received = 0
        exceeded = False
        responded = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal responded
            if not exceeded:
                await send(message)
            elif not responded:
                # Whatever the app made of the cut-off body, the client is told why.
                responded = True
                await too_large(scope, receive, send)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not responded:
            await too_large(scope, receive, send)
