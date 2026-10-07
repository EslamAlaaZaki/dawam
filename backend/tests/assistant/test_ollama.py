"""The assistant against a real small model served by Ollama (marker ``ollama``).

Not part of the normal run: CI's ``assistant-ollama`` job starts Ollama, pulls a small
tool-capable model and runs ``pytest -m ollama``. Locally, set ``DAWAM_OLLAMA_URL`` (for
example ``http://localhost:11434/v1``) and ``DAWAM_OLLAMA_MODEL`` to try it.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import pytest

from dawam.modules.assistant.internal.agent import Finished, ToolOutcome, run_agent
from dawam.modules.llm import AdapterConfig, Capabilities, Gateway, Message, ToolSpec, adapter_for

pytestmark = pytest.mark.ollama

URL = os.environ.get("DAWAM_OLLAMA_URL", "")
MODEL = os.environ.get("DAWAM_OLLAMA_MODEL", "qwen2.5:1.5b")
CODE = "4711"
SECRET = ToolSpec(
    "get_secret_code",
    "Returns the secret code. Takes no arguments.",
    {"type": "object", "properties": {}, "additionalProperties": False},
)


class SecretTools:
    def __init__(self) -> None:
        self.ran = 0

    def specs(self) -> Sequence[ToolSpec]:
        return [SECRET]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.ran += 1
        return ToolOutcome(f"The secret code is {CODE}.", "ok")


def ask(capabilities: Capabilities) -> tuple[SecretTools, Finished]:
    adapter = adapter_for(
        "openai_compatible", AdapterConfig(base_url=URL, api_key=None, timeout_seconds=300)
    )
    gateway = Gateway(adapter, model=MODEL, capabilities=capabilities)
    tools = SecretTools()
    events = list(
        run_agent(
            gateway,
            tools,
            system="You are a helpful assistant. Use your tools to look things up.",
            history=[Message("user", "Call get_secret_code and tell me the code it returns.")],
            max_tool_calls=3,
        )
    )
    finished = events[-1]
    assert isinstance(finished, Finished)
    return tools, finished


@pytest.mark.skipif(not URL, reason="DAWAM_OLLAMA_URL is not set")
def test_a_small_tool_capable_model_calls_a_native_tool_and_answers():
    tools, finished = ask(Capabilities(tool_calling=True, context_window=4096))

    assert finished.status in ("completed", "tool_limit"), finished.error_message
    assert tools.ran >= 1 and CODE in finished.text


@pytest.mark.skipif(not URL, reason="DAWAM_OLLAMA_URL is not set")
def test_limited_mode_ends_cleanly_with_a_small_model():
    """A small model may not follow the JSON protocol; the run must still end in a defined
    state (an answer, or the strict parser giving up), never hang or raise."""
    tools, finished = ask(Capabilities(tool_calling=False, context_window=4096))

    assert finished.status in ("completed", "tool_limit", "failed")
    if finished.status == "failed":
        assert finished.error_code == "invalid_tool_call" and tools.ran == 0
