"""Replaying recorded provider responses, for the adapter contract suite.

A fixture is a JSON file ``{"status", "headers", "body"}`` (or ``"body_lines"`` for a
server-sent-events stream), recorded from a real provider's reply and kept as is.
``ReplayTransport`` answers each request with the next response it was given and keeps
the requests, so a test can assert on what the adapter put on the wire.
"""

from __future__ import annotations

import io
import json
import struct
import zlib
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


def eventstream_frame(event_type: str, payload: Any, message_type: str = "event") -> bytes:
    """One AWS event-stream frame (Bedrock's streaming wire format), CRCs included."""

    def header(name: str, value: str) -> bytes:
        raw_name, raw_value = name.encode(), value.encode()
        return (
            bytes([len(raw_name)])
            + raw_name
            + b"\x07"
            + struct.pack(">H", len(raw_value))
            + raw_value
        )

    kind = ":event-type" if message_type == "event" else ":exception-type"
    headers = (
        header(":message-type", message_type)
        + header(kind, event_type)
        + header(":content-type", "application/json")
    )
    body = json.dumps(payload).encode("utf-8")
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


def load(directory: Path, name: str) -> HttpResponse:
    data = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    if "body_events" in data:
        raw = b"".join(
            eventstream_frame(e["type"], e["payload"], e.get("message_type", "event"))
            for e in data["body_events"]
        )
    elif "body_lines" in data:
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
