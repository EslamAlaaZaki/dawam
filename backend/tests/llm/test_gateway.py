"""The gateway on top of an adapter: retries with backoff, usage, and the capability
probe. Driven through the scripted fake provider, so no network or database is needed."""

from __future__ import annotations

import io

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
from dawam.modules.llm.internal.transport import HttpResponse
from dawam.modules.llm.internal.wire import error_for

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


# -- an agent or light model is only marked "no" when that is confirmed (#552) ------------

PLAIN = Reply(text="OK")
ECHO = Reply(tool_calls=(ToolCall("c1", "echo", {"text": "ping"}),))
TEXT_ANSWER = Reply(text="ping")
JSON_OK = Reply(text='{"ok": true}')


def agent_probe(*script: Reply | LlmError, roles: bool = True):
    llm, _ = gateway(FakeAdapter().script(*script))
    return probe(llm, chat=True, embedding=False, confirmed_only=roles)


@pytest.mark.parametrize("error", [LlmError("rate_limit", "slow down"), LlmError("auth", "bad")])
def test_a_temporary_tool_error_leaves_tool_calling_unknown_with_a_warning(error):
    outcomes = [PLAIN, PLAIN] + [error] * 8
    result = agent_probe(*outcomes)

    assert result.ok and result.capabilities.tool_calling is None
    assert any("tool calling" in w.lower() and error.message in w for w in result.warnings)


def test_a_provider_rejecting_tools_confirms_no_tool_calling():
    result = agent_probe(PLAIN, PLAIN, LlmError("bad_request", "tools are not supported"))

    assert result.capabilities.tool_calling is False and not result.warnings


def test_text_answers_to_both_echo_attempts_confirm_no_tool_calling():
    result = agent_probe(PLAIN, PLAIN, TEXT_ANSWER, TEXT_ANSWER)

    assert result.capabilities.tool_calling is False


def test_the_echo_call_on_the_second_attempt_is_enough():
    result = agent_probe(PLAIN, PLAIN, TEXT_ANSWER, ECHO)

    assert result.capabilities.tool_calling is True


@pytest.mark.parametrize("error", [LlmError("unavailable", "overloaded"), LlmError("auth", "bad")])
def test_a_temporary_schema_error_leaves_json_schema_unknown_with_a_warning(error):
    result = agent_probe(PLAIN, PLAIN, ECHO, *[error] * 8)

    assert result.capabilities.tool_calling is True and result.capabilities.json_schema is None
    assert any("json schema" in w.lower() and error.message in w for w in result.warnings)


def test_a_provider_rejecting_the_schema_confirms_no_json_schema():
    result = agent_probe(PLAIN, PLAIN, ECHO, LlmError("bad_request", "response schema unsupported"))

    assert result.capabilities.json_schema is False and not result.warnings


def test_plain_answers_to_both_schema_attempts_confirm_no_json_schema():
    result = agent_probe(PLAIN, PLAIN, ECHO, TEXT_ANSWER, TEXT_ANSWER)

    assert result.capabilities.json_schema is False


def test_json_on_the_second_schema_attempt_confirms_support():
    result = agent_probe(PLAIN, PLAIN, ECHO, TEXT_ANSWER, JSON_OK)

    assert result.capabilities.json_schema is True


def test_other_roles_still_store_no_on_a_temporary_error():
    llm, _ = gateway(FakeAdapter().script(PLAIN, PLAIN, LlmError("auth", "bad")))

    result = probe(llm, chat=True, embedding=False)

    assert result.capabilities.tool_calling is False and not result.warnings


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


def test_a_request_too_large_is_a_context_overflow():
    # Groq's per-minute token limit answers 413 without any context-window wording (#533).
    body = (
        b'{"error": {"message": "Request too large for model on tokens per minute (TPM): '
        b'Limit 8000, Requested 8828, please reduce your message size and try again."}}'
    )

    error = error_for(HttpResponse(413, {}, io.BytesIO(body)))

    assert error.code == "context_overflow" and not error.retryable
    assert "Requested 8828" in error.message
