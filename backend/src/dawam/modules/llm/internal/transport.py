"""HTTP for the adapters, as a port so the contract suite can replay recorded responses.

``UrllibTransport`` is the real one (standard library only: no vendor SDK, no extra
dependency). It never raises for an HTTP error status: the adapter maps statuses to
``LlmError``. A connection failure or timeout is a ``TransportError``.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from typing import IO, Protocol


class TransportError(Exception):
    """The server could not be reached, or did not answer in time."""

    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


class HttpResponse:
    def __init__(self, status: int, headers: Mapping[str, str], body: IO[bytes]) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self._body = body

    def read(self) -> bytes:
        return self._body.read()

    def iter_lines(self) -> Iterator[bytes]:
        for line in self._body:
            yield line.rstrip(b"\r\n")

    def close(self) -> None:
        self._body.close()


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, dict(exc.headers.items()), exc)
        except TimeoutError:
            raise TransportError("The provider did not answer in time.", timeout=True) from None
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, TimeoutError)
            message = (
                "The provider did not answer in time."
                if timed_out
                else "The provider could not be reached."
            )
            raise TransportError(message, timeout=timed_out) from None
        except OSError:
            raise TransportError("The provider could not be reached.") from None
        return HttpResponse(response.status, dict(response.headers.items()), response)
