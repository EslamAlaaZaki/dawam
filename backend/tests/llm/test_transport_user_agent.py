"""The real transport names DAWAM in its User-Agent; a Cloudflare block is not a key error."""

from __future__ import annotations

import io

import dawam
from dawam.modules.llm.gateway import AUTH
from dawam.modules.llm.internal.transport import HttpResponse, UrllibTransport
from dawam.modules.llm.internal.wire import error_for
from tests.llm.stub_server import StubLlmServer


def _user_agent_sent(headers: dict[str, str]) -> str | None:
    stub = StubLlmServer()
    with stub.running():
        UrllibTransport().request(
            "GET", f"{stub.base_url}/models", headers=headers, body=None, timeout=5
        )
    return stub.requests[0]["user_agent"]


def test_sends_a_dawam_user_agent_by_default() -> None:
    assert _user_agent_sent({}) == f"dawam/{dawam.__version__}"


def test_keeps_the_user_agent_an_adapter_set() -> None:
    assert _user_agent_sent({"user-agent": "custom/2"}) == "custom/2"


def test_cloudflare_browser_block_is_not_an_api_key_rejection() -> None:
    body = b"error code: 1010 The site owner has blocked access based on your browser's signature."
    error = error_for(HttpResponse(403, {}, io.BytesIO(body)))
    assert error.code != AUTH
    assert "API key" not in error.message


def test_a_plain_403_is_still_an_api_key_rejection() -> None:
    error = error_for(HttpResponse(403, {}, io.BytesIO(b'{"error": "forbidden"}')))
    assert error.code == AUTH
