"""The adapter registry: which ``Adapter`` serves a provider's ``adapter`` kind.

Built-in kinds are listed here; further ones ship as separate packages through the
``dawam.llm_providers`` entry point group. An entry point's name is the adapter kind and
it loads a ``PluginAdapterFactory``. A plugin cannot replace a built-in kind, and a
plugin that fails to load is skipped (it must not take the gateway down).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import entry_points

from ..gateway import Adapter
from .anthropic import AnthropicAdapter
from .azure_openai import AzureOpenAIAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .transport import Transport, UrllibTransport

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "dawam.llm_providers"
BUILTIN_ADAPTERS: dict[str, type] = {
    "openai_compatible": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
    "azure_openai": AzureOpenAIAdapter,
}
ADAPTER_KINDS = tuple(BUILTIN_ADAPTERS)


@dataclass(frozen=True)
class AdapterConfig:
    """What an adapter needs to reach one provider. The key is in clear, only in memory."""

    base_url: str = field(repr=False)
    api_key: str | None = field(repr=False)
    timeout_seconds: int


AdapterFactory = Callable[[str, AdapterConfig], Adapter]
"""Builds the adapter for a provider kind; tests substitute a fake."""

PluginAdapterFactory = Callable[[AdapterConfig, Transport], Adapter]
"""What a ``dawam.llm_providers`` entry point loads."""


@lru_cache(maxsize=1)
def plugin_factories() -> dict[str, PluginAdapterFactory]:
    found: dict[str, PluginAdapterFactory] = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in BUILTIN_ADAPTERS:
            continue
        try:
            found[entry.name] = entry.load()
        except Exception as exc:
            log.warning(
                "LLM provider plugin %r could not be loaded (%s)", entry.name, type(exc).__name__
            )
    return found


def adapter_kinds() -> tuple[str, ...]:
    return ADAPTER_KINDS + tuple(sorted(plugin_factories()))


def adapter_for(kind: str, config: AdapterConfig, transport: Transport | None = None) -> Adapter:
    transport = transport or UrllibTransport()
    if kind in BUILTIN_ADAPTERS:
        return BUILTIN_ADAPTERS[kind](
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            transport=transport,
        )
    factory = plugin_factories().get(kind)
    if factory is None:
        raise ValueError(f"unknown LLM adapter {kind!r}")
    return factory(config, transport)
