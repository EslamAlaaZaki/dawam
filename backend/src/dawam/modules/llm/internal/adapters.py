"""The adapter registry: which ``Adapter`` serves a provider's ``adapter`` kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..gateway import Adapter
from .anthropic import AnthropicAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .transport import Transport, UrllibTransport

ADAPTER_KINDS = ("openai_compatible", "anthropic")


@dataclass(frozen=True)
class AdapterConfig:
    """What an adapter needs to reach one provider. The key is in clear, only in memory."""

    base_url: str = field(repr=False)
    api_key: str | None = field(repr=False)
    timeout_seconds: int


AdapterFactory = Callable[[str, AdapterConfig], Adapter]
"""Builds the adapter for a provider kind; tests substitute a fake."""


def adapter_for(kind: str, config: AdapterConfig, transport: Transport | None = None) -> Adapter:
    if kind == "openai_compatible":
        return OpenAICompatibleAdapter(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            transport=transport or UrllibTransport(),
        )
    if kind == "anthropic":
        return AnthropicAdapter(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            transport=transport or UrllibTransport(),
        )
    raise ValueError(f"unknown LLM adapter {kind!r}")
