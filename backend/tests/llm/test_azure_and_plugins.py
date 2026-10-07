"""What is specific to Azure OpenAI and to provider plugins (shared behaviour: contract suite)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl

import pytest

from dawam.modules.llm import AdapterConfig, LlmError, Message, adapter_for
from dawam.modules.llm.internal import adapters
from dawam.modules.llm.internal.azure_openai import AzureOpenAIAdapter
from dawam.modules.llm.internal.transport import HttpResponse
from tests.llm.replay import ReplayTransport, load

FIXTURES = Path(__file__).parent / "fixtures" / "azure_openai"


def _adapter(base_url="https://res.openai.azure.com", api_key="k", **extra):
    transport = ReplayTransport(load(FIXTURES, "chat_text"), load(FIXTURES, "embed"))
    adapter = AzureOpenAIAdapter(
        base_url=base_url, api_key=api_key, timeout_seconds=9, transport=transport, **extra
    )
    return adapter, transport


def test_the_api_version_in_the_endpoint_is_used_and_the_deployment_is_in_the_path():
    adapter, transport = _adapter("https://res.openai.azure.com/?api-version=2025-01-01-preview")

    list(adapter.chat("my gpt", [Message("user", "Hi")], [], False, None))

    assert transport.requests[0].url == (
        "https://res.openai.azure.com/openai/deployments/my%20gpt/chat/completions"
        "?api-version=2025-01-01-preview"
    )


class _Transport:
    """A fake HTTP transport that answers by URL with queued ``(status, body)`` pairs."""

    def __init__(self, token_lifetimes=(3600,), chat_statuses=(200,)):
        self.tokens = [
            (200, {"access_token": f"tok{i}", "expires_in": n})
            for i, n in enumerate(token_lifetimes, 1)
        ]
        self.chats = list(chat_statuses)
        self.calls: list[tuple[str, dict, bytes]] = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((url, dict(headers), body))
        if "login.microsoftonline.com" in url:
            status, payload = self.tokens.pop(0)
        else:
            status = self.chats.pop(0)
            payload = (
                json.loads((FIXTURES / "chat_text.json").read_text())["body"]
                if status == 200
                else {"error": {"message": "Access denied for tok1 and s3cret"}}
            )
        return HttpResponse(status, {}, io.BytesIO(json.dumps(payload).encode()))

    @property
    def token_calls(self):
        return [c for c in self.calls if "login.microsoftonline.com" in c[0]]

    @property
    def chat_calls(self):
        return [c for c in self.calls if "login.microsoftonline.com" not in c[0]]


class _Clock:
    now = 0.0

    def __call__(self):
        return self.now


def _entra(transport, clock=None):
    return AzureOpenAIAdapter(
        base_url="https://res.openai.azure.com",
        api_key="entra:tenant-1:client-1:s3cret",
        timeout_seconds=9,
        transport=transport,
        clock=clock or _Clock(),
    )


def _ask(adapter):
    return list(adapter.chat("d", [Message("user", "Hi")], [], False, None))


def test_entra_id_fetches_a_client_credentials_token_and_sends_it_as_a_bearer():
    transport = _Transport()

    _ask(_entra(transport))

    url, _, body = transport.token_calls[0]
    assert url == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    form = dict(parse_qsl(body.decode()))
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "client-1",
        "client_secret": "s3cret",
        "scope": "https://cognitiveservices.azure.com/.default",
    }
    headers = transport.chat_calls[0][1]
    assert headers["Authorization"] == "Bearer tok1" and "api-key" not in headers


def test_the_token_is_reused_until_five_minutes_before_it_expires_then_refreshed():
    transport = _Transport(token_lifetimes=(3600, 3600), chat_statuses=(200, 200, 200))
    clock = _Clock()
    adapter = _entra(transport, clock)

    _ask(adapter)
    clock.now = 3299
    _ask(adapter)
    assert len(transport.token_calls) == 1
    clock.now = 3300
    _ask(adapter)

    assert len(transport.token_calls) == 2
    assert transport.chat_calls[2][1]["Authorization"] == "Bearer tok2"


def test_a_401_is_retried_once_with_a_fresh_token():
    transport = _Transport(token_lifetimes=(3600, 3600), chat_statuses=(401, 200))

    events = _ask(_entra(transport))

    assert events and len(transport.token_calls) == 2
    assert [c[1]["Authorization"] for c in transport.chat_calls] == ["Bearer tok1", "Bearer tok2"]


def test_a_second_401_is_an_auth_error_that_never_echoes_the_secret_or_token():
    transport = _Transport(token_lifetimes=(3600, 3600), chat_statuses=(401, 401))

    with pytest.raises(LlmError) as raised:
        _ask(_entra(transport))

    assert raised.value.code == "auth" and len(transport.chat_calls) == 2
    assert "s3cret" not in raised.value.message


def test_rejected_entra_credentials_are_an_auth_error_without_the_secret():
    transport = _Transport()
    transport.tokens = [(401, {"error": "invalid_client", "error_description": "s3cret"})]

    with pytest.raises(LlmError) as raised:
        _ask(_entra(transport))

    assert raised.value.code == "auth" and "s3cret" not in raised.value.message


def test_a_malformed_entra_credential_is_an_auth_error():
    adapter = AzureOpenAIAdapter(
        base_url="https://r",
        api_key="entra:only-a-token",
        timeout_seconds=9,
        transport=_Transport(),
    )

    with pytest.raises(LlmError) as raised:
        _ask(adapter)

    assert raised.value.code == "auth"


def test_a_token_provider_is_asked_for_a_fresh_token_per_request():
    tokens = iter(["t1", "t2"])
    adapter, transport = _adapter(api_key=None, token_provider=lambda: next(tokens))

    list(adapter.chat("d", [Message("user", "Hi")], [], False, None))
    adapter.embed("d", ["a", "b"])

    assert [r.headers["Authorization"] for r in transport.requests] == ["Bearer t1", "Bearer t2"]


def test_azure_is_a_built_in_kind():
    config = AdapterConfig(base_url="http://x", api_key="k", timeout_seconds=5)
    assert isinstance(adapter_for("azure_openai", config), AzureOpenAIAdapter)


@pytest.fixture(autouse=True)
def _fresh_plugin_cache():
    adapters.plugin_factories.cache_clear()
    yield
    adapters.plugin_factories.cache_clear()


def _entry(name, loader):
    return SimpleNamespace(name=name, load=loader)


def test_a_plugin_registers_a_kind_through_the_entry_point(monkeypatch):
    built = []

    def factory(config, transport):
        built.append(config.base_url)
        return "plugin-adapter"

    seen = {}

    def fake_entry_points(*, group):
        seen["group"] = group
        return [_entry("acme", lambda: factory)]

    monkeypatch.setattr(adapters, "entry_points", fake_entry_points)
    config = AdapterConfig(base_url="http://acme", api_key=None, timeout_seconds=5)

    assert "acme" in adapters.adapter_kinds()
    assert seen["group"] == "dawam.llm_providers"
    assert adapter_for("acme", config) == "plugin-adapter" and built == ["http://acme"]


def test_a_broken_or_shadowing_plugin_is_ignored(monkeypatch):
    def boom():
        raise ImportError("missing dependency")

    monkeypatch.setattr(
        adapters,
        "entry_points",
        lambda *, group: [_entry("broken", boom), _entry("anthropic", lambda: None)],
    )

    assert adapters.adapter_kinds() == adapters.ADAPTER_KINDS
    with pytest.raises(ValueError):
        adapter_for("broken", AdapterConfig("http://x", None, 5))
