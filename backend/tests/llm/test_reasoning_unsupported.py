"""Reasoning enabled on an adapter that has no reasoning support must not break the call (#409)."""

from __future__ import annotations

import inspect
from collections.abc import Iterator

from dawam.modules.llm import Done, Gateway, Message, ReasoningSettings, TextDelta, Usage
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter


class LegacyAdapter:
    """A plugin adapter written before reasoning models: ``chat`` has no ``reasoning``."""

    def chat(self, model, messages, tools, stream, json_schema) -> Iterator[object]:
        yield TextDelta("hi")
        yield Done(Usage(1, 1), "stop")


def test_a_legacy_adapter_still_works_with_reasoning_enabled():
    gateway = Gateway(LegacyAdapter(), model="m", reasoning=ReasoningSettings(show=True))  # type: ignore[arg-type]

    events = list(gateway.chat([Message("user", "Hi")]))

    assert events[0] == TextDelta("hi")


def test_bedrock_and_gemini_accept_and_ignore_reasoning_settings():
    for adapter in (BedrockAdapter, GeminiAdapter):
        assert "reasoning" in inspect.signature(adapter.chat).parameters
