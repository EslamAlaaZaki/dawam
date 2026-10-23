"""Cached input tokens: recorded where the provider reports them, never sent as extra fields.

The cached replays are built from the providers' documented usage formats (not recorded
from a live call).
"""

from __future__ import annotations

from pathlib import Path

from dawam.modules.llm import Message, ToolSpec, Usage
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from tests.llm.replay import ReplayTransport, load

FIXTURES = Path(__file__).parent / "fixtures"
TOOL = ToolSpec("echo", "Repeat.", {"type": "object", "properties": {}})


def _openai(name: str) -> tuple[OpenAICompatibleAdapter, ReplayTransport]:
    transport = ReplayTransport(load(FIXTURES / "openai_compatible", name))
    adapter = OpenAICompatibleAdapter(
        base_url="http://llm.test/v1", api_key="k", timeout_seconds=9, transport=transport
    )
    return adapter, transport


def _done(adapter, tools=()):
    return list(
        adapter.chat("m", [Message("system", "S"), Message("user", "Hi")], tools, False, None)
    )[-1]


def test_openai_cached_tokens_are_recorded_inside_the_prompt_count():
    adapter, transport = _openai("chat_text_cached")

    done = _done(adapter, [TOOL])

    assert done.usage == Usage(125, 3, cache_read_tokens=100)
    assert "cache_control" not in str(transport.requests[0].json)  # automatic prefix caching


def test_deepseek_cache_hits_are_recorded_as_cache_reads():
    adapter, _ = _openai("chat_text_deepseek_cached")

    assert _done(adapter).usage == Usage(125, 3, cache_read_tokens=100)


def test_a_reply_without_cache_fields_reports_no_cached_tokens():
    adapter, _ = _openai("chat_text")

    assert _done(adapter).usage == Usage(12, 3)


def test_gemini_cached_content_tokens_are_recorded_inside_the_prompt_count():
    transport = ReplayTransport(load(FIXTURES / "gemini", "chat_text_cached"))
    adapter = GeminiAdapter(
        base_url="http://llm.test/v1beta", api_key="k", timeout_seconds=9, transport=transport
    )

    assert _done(adapter).usage == Usage(125, 3, cache_read_tokens=100)
