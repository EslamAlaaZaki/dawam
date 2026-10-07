"""What is specific to Azure OpenAI and to provider plugins (shared behaviour: contract suite)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dawam.modules.llm import AdapterConfig, Message, adapter_for
from dawam.modules.llm.internal import adapters
from dawam.modules.llm.internal.azure_openai import AzureOpenAIAdapter
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


def test_an_entra_id_token_is_sent_as_a_bearer_token_instead_of_an_api_key():
    adapter, transport = _adapter(api_key="entra:tok123")

    list(adapter.chat("d", [Message("user", "Hi")], [], False, None))

    headers = transport.requests[0].headers
    assert headers["Authorization"] == "Bearer tok123" and "api-key" not in headers


def test_a_token_provider_is_asked_for_a_fresh_token_per_request():
    tokens = iter(["t1", "t2"])
    adapter, transport = _adapter(api_key=None, token_provider=lambda: next(tokens))

    list(adapter.chat("d", [Message("user", "Hi")], [], False, None))
    adapter.embed("d", ["a", "b"])

    assert [r.headers["Authorization"] for r in transport.requests] == ["Bearer t1", "Bearer t2"]


def test_azure_is_a_built_in_kind():
    config = AdapterConfig(base_url="http://x", api_key="k", timeout_seconds=5)
    assert isinstance(adapter_for("azure_openai", config), AzureOpenAIAdapter)


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
