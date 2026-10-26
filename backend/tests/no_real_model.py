"""Test guard: no test reaches a real model endpoint.

Model calls in tests go through ``FakeAdapter``, ``ReplayTransport`` or ``StubLlmServer``.
Name resolution is how every connection starts, so refusing provider hosts there stops
them all; localhost (Testcontainers, stub servers, Ollama in CI) is untouched. Tests
marked ``real_model`` (opt-in, never run in CI) are exempt.
"""

from __future__ import annotations

import re
import socket
from typing import Any

import pytest

REAL_MODEL_HOSTS = re.compile(
    r"(?:^|\.)(?:api\.anthropic\.com|api\.openai\.com|openai\.azure\.com"
    r"|cognitiveservices\.azure\.com|generativelanguage\.googleapis\.com"
    r"|aiplatform\.googleapis\.com|api\.mistral\.ai|api\.cohere\.com|api\.cohere\.ai"
    r"|api\.groq\.com|api\.x\.ai|api\.together\.xyz|api\.deepseek\.com|openrouter\.ai"
    r"|bedrock(?:-runtime|-agent-runtime)?\.[a-z0-9-]+\.amazonaws\.com"
    # Web search providers (ADR 0008): faked at the web module's ``Network`` port.
    r"|api\.langsearch\.com)\.?$",
    re.IGNORECASE,
)


class RealModelCallError(AssertionError):
    """A test tried to reach a real model endpoint."""


def _host_of(host: Any) -> str:
    return host.decode() if isinstance(host, bytes) else str(host or "")


@pytest.fixture(autouse=True)
def _no_real_model_calls(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_model"):
        return
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(host: Any) -> None:
        if REAL_MODEL_HOSTS.search(_host_of(host)):
            raise RealModelCallError(
                f"test tried to reach a real model endpoint ({_host_of(host)}); "
                "use FakeAdapter, ReplayTransport or StubLlmServer"
            )

    def address_host(address: Any) -> Any:
        return address[0] if isinstance(address, tuple) and address else None

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host: Any) -> Any:
        check(host)
        return real_gethostbyname(host)

    def gethostbyname_ex(host: Any) -> Any:
        check(host)
        return real_gethostbyname_ex(host)

    def connect(self: socket.socket, address: Any) -> Any:
        check(address_host(address))
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        check(address_host(address))
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname_ex)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
