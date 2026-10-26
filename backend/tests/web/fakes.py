"""A fake ``Network`` for the web module: the internet, scripted. Nothing real is reached."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dawam.modules.web import HttpReply, HttpRequest, NetworkError

Responder = Callable[[HttpRequest], HttpReply]


def reply(
    body: bytes | str = b"",
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    *,
    content_type: str = "text/html; charset=utf-8",
) -> HttpReply:
    data = body.encode() if isinstance(body, str) else body
    return HttpReply(status, {"content-type": content_type, **(headers or {})}, data)


@dataclass
class FakeNetwork:
    hosts: dict[str, list[str]] = field(default_factory=dict)
    """Host name -> the addresses it resolves to; an unknown host does not resolve."""
    pages: dict[str, HttpReply | Responder] = field(default_factory=dict)
    """URL (without the query) -> its reply, or a function of the request."""
    sent: list[HttpRequest] = field(default_factory=list)

    def resolve(self, host: str, port: int) -> list[str]:
        if host in self.hosts:
            return list(self.hosts[host])
        raise NetworkError("The host name could not be resolved.")

    def send(self, request: HttpRequest) -> HttpReply:
        self.sent.append(request)
        parts = urlsplit(request.url)
        key = f"{parts.scheme}://{parts.netloc}{parts.path}"
        page = self.pages.get(key)
        if page is None:
            raise NetworkError("The server could not be reached.")
        got = page(request) if callable(page) else page
        # Like the real network: at most ``max_bytes`` of the body are read.
        if len(got.body) > request.max_bytes:
            return HttpReply(got.status, got.headers, got.body[: request.max_bytes], True)
        return got
