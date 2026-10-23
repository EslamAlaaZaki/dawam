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
    r"|bedrock(?:-runtime|-agent-runtime)?\.[a-z0-9-]+\.amazonaws\.com)\.?$",
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

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if REAL_MODEL_HOSTS.search(_host_of(host)):
            raise RealModelCallError(
                f"test tried to reach a real model endpoint ({_host_of(host)}); "
                "use FakeAdapter, ReplayTransport or StubLlmServer"
            )
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
