"""A reasoning model in the agent loop (#409): reasoning streams when its model shows it, is
hidden otherwise, and never becomes part of the saved answer."""

from __future__ import annotations

from typing import Any

from dawam.modules.assistant.internal.agent import Finished, Reasoning, Text, run_agent
from dawam.modules.llm import FakeAdapter, Gateway, Message, ReasoningSettings, Reply, Usage


class NoTools:
    def specs(self) -> list[Any]:
        return []

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:  # pragma: no cover
        raise AssertionError("no tool should run")


def _run(show: bool) -> tuple[list[Any], FakeAdapter]:
    adapter = FakeAdapter().script(
        Reply("Blue.", reasoning="Rayleigh scattering.", usage=Usage(10, 40, reasoning_tokens=25))
    )
    gateway = Gateway(adapter, model="m", reasoning=ReasoningSettings(effort="low", show=show))
    events = list(run_agent(gateway, NoTools(), system="S", history=[Message("user", "Why?")]))
    return events, adapter


def test_reasoning_streams_when_the_model_shows_it_and_stays_out_of_the_answer():
    events, adapter = _run(show=True)

    assert [e.text for e in events if isinstance(e, Reasoning)] == ["Rayleigh scattering."]
    finished = events[-1]
    assert isinstance(finished, Finished) and finished.text == "Blue."
    assert [e.text for e in events if isinstance(e, Text)] == ["Blue."]
    assert adapter.calls[0].reasoning == ReasoningSettings(effort="low", show=True)


def test_reasoning_is_hidden_when_the_model_does_not_show_it():
    events, _ = _run(show=False)

    assert not any(isinstance(e, Reasoning) for e in events)
    finished = events[-1]
    assert isinstance(finished, Finished) and finished.completion_tokens == 40
