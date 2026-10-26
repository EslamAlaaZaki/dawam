"""Assistant tests: the fake provider reports a window big enough for the real tool set,
whose schemas now count against the prompt budget."""

from __future__ import annotations

import pytest

from dawam.app import Services
from dawam.modules.assistant.internal import agent
from dawam.modules.llm import FakeAdapter


@pytest.fixture
def fake_llm(services: Services) -> FakeAdapter:
    fake = FakeAdapter(context_window=32_768)
    services.llm_adapters = lambda kind, config: fake
    return fake


@pytest.fixture(autouse=True)
def all_tools_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests check which tools a role is offered: send them all (tool search has its own tests)."""
    monkeypatch.setattr(agent, "TOOL_SEARCH_THRESHOLD", 1_000)
