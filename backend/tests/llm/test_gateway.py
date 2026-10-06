"""The gateway on top of an adapter: retries with backoff, usage, and the capability
probe. Driven through the scripted fake provider, so no network or database is needed."""

from __future__ import annotations

import pytest

from dawam.modules.llm import (
    Done,
    FakeAdapter,
    Gateway,
    LlmError,
    Message,
    Reply,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    Usage,
)
from dawam.modules.llm.gateway import probe

ASK = [Message("user", "Hi")]


def gateway(adapter: FakeAdapter, *, max_retries: int = 3) -> tuple[Gateway, list[float]]:
    sleeps: list[float] = []
    return Gateway(adapter, model="m", max_retries=max_retries, sleep=sleeps.append), sleeps


def test_a_scripted_reply_and_tool_call_are_replayed_in_the_internal_format():
    call = ToolCall("c1", "lookup", {"id": 7})
    fake = FakeAdapter().script(Reply(text="One moment.", tool_calls=(call,)))
    llm, _ = gateway(fake)

    events = list(llm.chat(ASK, [ToolSpec("lookup", "Find it.", {"type": "object"})]))

    assert events == [
        TextDelta("One moment."),
        ToolCallEvent(call),
        Done(Usage(10, 5), "tool_calls"),
    ]
    assert fake.calls[0].messages == tuple(ASK) and fake.calls[0].tools[0].name == "lookup"


def test_a_stream_arrives_as_several_text_deltas():
    fake = FakeAdapter().script(Reply(text="a b c"))
    llm, _ = gateway(fake)

    events = list(llm.chat(ASK, stream=True))

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["a", " b", " c"]


def test_usage_is_estimated_when_the_provider_reports_none():
    fake = FakeAdapter().script(Reply(text="x" * 40, usage=None))
    llm, _ = gateway(fake)

    *_, done = llm.chat([Message("user", "y" * 80)])

    assert done == Done(Usage(20, 10, estimated=True), "stop")


def test_reported_usage_is_passed_through_untouched():
    llm, _ = gateway(FakeAdapter().script(Reply(text="ok", usage=Usage(3, 2))))

    *_, done = llm.chat(ASK)

    assert done.usage == Usage(3, 2) and not done.usage.estimated


def test_rate_limits_and_unavailability_are_retried_with_growing_backoff():
    fake = FakeAdapter().script(
        LlmError("rate_limit", "slow down"),
        LlmError("unavailable", "down"),
        LlmError("unavailable", "down"),
        Reply(text="finally"),
    )
    llm, sleeps = gateway(fake)

    events = list(llm.chat(ASK))

    assert events[0] == TextDelta("finally")
    assert len(fake.calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_a_retry_after_from_the_provider_sets_the_wait():
    fake = FakeAdapter().script(
        LlmError("rate_limit", "slow down", retry_after=7), Reply(text="ok")
    )
    llm, sleeps = gateway(fake)

    list(llm.chat(ASK))

    assert sleeps == [7]


def test_retries_stop_after_the_limit_and_the_error_surfaces():
    fake = FakeAdapter().script(*[LlmError("unavailable", "down")] * 5)
    llm, sleeps = gateway(fake, max_retries=2)

    with pytest.raises(LlmError) as raised:
        list(llm.chat(ASK))

    assert raised.value.code == "unavailable"
    assert len(fake.calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize("code", ["auth", "context_overflow", "bad_request"])
def test_other_errors_are_not_retried(code):
    fake = FakeAdapter().script(LlmError(code, "no"), Reply(text="unused"))
    llm, sleeps = gateway(fake)

    with pytest.raises(LlmError) as raised:
        list(llm.chat(ASK))

    assert raised.value.code == code
    assert len(fake.calls) == 1 and sleeps == []


def test_embeddings_are_retried_too():
    fake = FakeAdapter().script(LlmError("rate_limit", "slow down"))
    llm, sleeps = gateway(fake)

    assert llm.embed(["a", "b"]) == [[0.1] * 4, [0.1] * 4]
    assert len(sleeps) == 1


def test_probe_records_what_a_capable_model_supports():
    llm, _ = gateway(FakeAdapter(context_window=32768))

    result = probe(llm, chat=True, embedding=False)

    assert result.ok
    caps = result.capabilities
    assert (caps.tool_calling, caps.streaming, caps.json_schema) == (True, True, True)
    assert caps.context_window == 32768 and caps.embedding_dimension is None


def test_probe_marks_missing_features_without_failing_the_test():
    llm, _ = gateway(FakeAdapter(tool_calling=False, streaming=False, json_schema=False))

    result = probe(llm, chat=True, embedding=False)

    assert result.ok
    caps = result.capabilities
    assert (caps.tool_calling, caps.streaming, caps.json_schema) == (False, False, False)


def test_the_admins_context_window_wins_over_the_servers():
    llm, _ = gateway(FakeAdapter(context_window=32768))

    assert (
        probe(llm, chat=True, embedding=False, context_window=4096).capabilities.context_window
        == 4096
    )


def test_probe_fails_with_the_providers_error_code():
    llm, _ = gateway(FakeAdapter().script(LlmError("auth", "bad key")))

    result = probe(llm, chat=True, embedding=False)

    assert (result.ok, result.error_code, result.error) == (False, "auth", "bad key")


def test_probe_reports_an_embedding_models_dimension():
    llm, _ = gateway(FakeAdapter(embedding_dimension=768))

    result = probe(llm, chat=False, embedding=True)

    assert result.ok and result.capabilities.embedding_dimension == 768
