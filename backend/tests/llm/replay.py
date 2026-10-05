"""Replaying recorded provider responses, for the adapter contract suite.

A fixture is a JSON file ``{"status", "headers", "body"}`` (or ``"body_lines"`` for a
server-sent-events stream), recorded from a real provider's reply and kept as is.
``ReplayTransport`` answers each request with the next response it was given and keeps
the requests, so a test can assert on what the adapter put on the wire.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dawam.modules.llm.internal.transport import HttpResponse, TransportError


@dataclass(frozen=True)
class Recorded:
    method: str
    url: str
    headers: Mapping[str, str]
    json: Any
    timeout: float


def load(directory: Path, name: str) -> HttpResponse:
    data = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    if "body_lines" in data:
        raw = "\n".join(data["body_lines"]).encode("utf-8")
    else:
        raw = json.dumps(data["body"]).encode("utf-8")
    return HttpResponse(data["status"], data["headers"], io.BytesIO(raw))


class ReplayTransport:
    def __init__(self, *responses: HttpResponse | TransportError) -> None:
        self._responses = list(responses)
        self.requests: list[Recorded] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        self.requests.append(
            Recorded(method, url, dict(headers), json.loads(body) if body else None, timeout)
        )
        response = self._responses.pop(0)
        if isinstance(response, TransportError):
            raise response
        return response
