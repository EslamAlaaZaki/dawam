"""The network, as a port: name resolution and one HTTP exchange.

Everything the web module sends goes through ``Network``, so tests fake it and CI never
reaches a search provider or a web page. ``SystemNetwork`` is the real one (standard
library only). A request may name the ``address`` to connect to: web fetch resolves and
checks a host itself, then connects to the address it checked, so a second lookup (DNS
rebinding) cannot swap in another. TLS is still verified against the URL's host name.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

READ_CHUNK = 64 * 1024


class NetworkError(Exception):
    """The server could not be reached, or did not answer in time."""

    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)  # may hold an API key
    body: bytes | None = None
    timeout: float = 10.0
    max_bytes: int = 1_000_000
    """Read at most this many bytes of the body (``HttpReply.truncated`` says if more came)."""
    address: str | None = None
    """The IP address to connect to; ``None``: resolve the URL's host as usual."""


@dataclass(frozen=True)
class HttpReply:
    status: int
    headers: Mapping[str, str]
    """Header names in lower case."""
    body: bytes
    truncated: bool = False


class Network(Protocol):
    def resolve(self, host: str, port: int) -> list[str]:
        """Every address ``host`` resolves to; ``NetworkError`` when it resolves to none."""
        ...

    def send(self, request: HttpRequest) -> HttpReply:
        """One exchange: redirects are not followed, an HTTP error status is a reply."""
        ...


class SystemNetwork:
    def resolve(self, host: str, port: int) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            raise NetworkError("The host name could not be resolved.") from None
        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            raise NetworkError("The host name could not be resolved.")
        return list(dict.fromkeys(addresses))

    def send(self, request: HttpRequest) -> HttpReply:
        deadline = time.monotonic() + request.timeout  # the whole exchange, not each read
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        https = parts.scheme == "https"
        port = parts.port or (443 if https else 80)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        connection: http.client.HTTPConnection = (
            _PinnedHttps(host, port, request.address, timeout=request.timeout)
            if https
            else _PinnedHttp(host, port, request.address, timeout=request.timeout)
        )
        try:
            connection.request(
                request.method, target, body=request.body, headers=dict(request.headers)
            )
            sock = connection.sock  # getresponse may drop it from the connection
            _spend(sock, deadline)
            response = connection.getresponse()
            body = _read(response, request.max_bytes + 1, deadline, sock)
            headers = {key.lower(): value for key, value in response.getheaders()}
            return HttpReply(
                response.status,
                headers,
                body[: request.max_bytes],
                truncated=len(body) > request.max_bytes,
            )
        except TimeoutError:
            raise NetworkError("The server did not answer in time.", timeout=True) from None
        except (OSError, http.client.HTTPException, ValueError):
            # ValueError: a URL or header http.client refuses; its message quotes the value,
            # which may be an API key, so it goes no further.
            raise NetworkError("The server could not be reached.") from None
        finally:
            connection.close()


def _spend(sock: socket.socket, deadline: float) -> None:
    """The next read on ``sock`` waits at most until ``deadline``."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    if sock.fileno() != -1:  # the end of the body closes it
        sock.settimeout(left)


def _read(
    response: http.client.HTTPResponse, limit: int, deadline: float, sock: socket.socket
) -> bytes:
    """Up to ``limit`` bytes of the body, chunk by chunk: a server that trickles its body
    still runs out of time (the socket timeout alone applies to each read)."""
    chunks: list[bytes] = []
    size = 0
    while size < limit:
        _spend(sock, deadline)
        chunk = response.read1(min(READ_CHUNK, limit - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


class _PinnedHttp(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str | None, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address or self.host, self.port), self.timeout)


class _PinnedHttps(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str | None, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address or self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
