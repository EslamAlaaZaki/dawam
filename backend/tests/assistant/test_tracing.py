"""OpenTelemetry spans of the AI side, read back from an in-memory exporter (no collector, no
network), and the prompt version kept on each run."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import Session

from dawam.modules.assistant.internal.agent import ToolOutcome, run_agent
from dawam.modules.assistant.internal.prompt import prompt_version
from dawam.modules.assistant.tables import RunRecord
from dawam.modules.llm import FakeAdapter, Gateway, Message, Reply, ToolCall, ToolSpec, Usage
from dawam.modules.llm.role_service import MeteredGateway
from dawam.platform.db import create_engine
from dawam.platform.tracing import Tracing, configure_tracing, set_tracing
from tests.assistant.test_chat import ask, conversation, model  # noqa: F401
from tests.roles import RoleClients

LOOKUP = ToolSpec("lookup", "Find it.", {"type": "object"})
EMAIL = "someone.private@example.com"


class _Usage:
    def ensure_within_budget(self, workspace_id: Any) -> None:
        pass

    def ensure_within_rate_limit(self, workspace_id: Any, user_id: Any) -> None:
        pass

    def record_usage(self, usage: Usage, **_: Any) -> None:
        pass


class _Tools:
    def specs(self) -> Sequence[ToolSpec]:
        return [LOOKUP]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(f"contact {EMAIL}", "ok")


@pytest.fixture
def spans(request: pytest.FixtureRequest) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    settings = SimpleNamespace(
        otel_endpoint=None,
        otel_capture_content=getattr(request, "param", False),
        otel_service_name="dawam-test",
    )
    configure_tracing(settings, exporter=exporter)  # type: ignore[arg-type]
    yield exporter
    set_tracing(Tracing())


def answer() -> None:
    adapter = FakeAdapter().script(
        Reply(tool_calls=(ToolCall("c1", "lookup", {"q": "orders"}),), usage=Usage(10, 2)),
        Reply(text="It is here", usage=Usage(20, 5)),
    )
    gateway = MeteredGateway(
        Gateway(adapter, model="m", sleep=lambda _: None),
        _Usage(),  # type: ignore[arg-type]
        role="agent",
        model_name="the-model",
        workspace_id=None,
        user_id=None,
    )
    list(run_agent(gateway, _Tools(), system="SYSTEM", history=[Message("user", "Where is it?")]))


def test_spans_carry_model_tokens_and_tool_name_but_no_content_by_default(spans):
    answer()

    chats = [s for s in spans.get_finished_spans() if s.name == "chat the-model"]
    [tool_span] = [s for s in spans.get_finished_spans() if s.name == "execute_tool lookup"]
    assert [s.attributes["gen_ai.request.model"] for s in chats] == ["the-model"] * 2
    assert [s.attributes["gen_ai.usage.input_tokens"] for s in chats] == [10, 20]
    assert [s.attributes["gen_ai.usage.output_tokens"] for s in chats] == [2, 5]
    assert tool_span.attributes["gen_ai.tool.name"] == "lookup"
    assert tool_span.end_time > tool_span.start_time  # latency
    assert all(s.end_time >= s.start_time for s in chats)
    everything = json.dumps([dict(s.attributes) for s in spans.get_finished_spans()])
    for secret in ("Where is it?", "It is here", "orders", EMAIL, "SYSTEM"):
        assert secret not in everything


@pytest.mark.parametrize("spans", [True], indirect=True)
def test_content_is_present_but_redacted_when_the_admin_turns_it_on(spans):
    answer()

    finished = spans.get_finished_spans()
    [tool_span] = [s for s in finished if s.name == "execute_tool lookup"]
    assert "orders" in tool_span.attributes["gen_ai.tool.call.arguments"]
    assert "contact" in tool_span.attributes["gen_ai.tool.call.result"]
    assert EMAIL not in json.dumps([dict(s.attributes) for s in finished])
    [last] = [s for s in finished if s.attributes.get("gen_ai.output.messages") == "It is here"]
    assert "Where is it?" in last.attributes["gen_ai.input.messages"]


def test_tracing_is_off_without_an_endpoint():
    settings = SimpleNamespace(otel_endpoint=None, otel_capture_content=True, otel_service_name="x")

    assert not configure_tracing(settings).enabled  # type: ignore[arg-type]


def test_the_prompt_version_changes_when_a_prompt_or_tool_changes():
    version = prompt_version("a", [LOOKUP])

    assert version == prompt_version("a", [LOOKUP])
    assert version != prompt_version("b", [LOOKUP])
    assert version != prompt_version("a", [ToolSpec("lookup", "Find it fast.", {})])


def test_each_run_records_its_prompt_version(roles: RoleClients, model, fake_llm, settings):  # noqa: F811
    fake_llm.script(Reply(text="hi"))
    owner = roles.client("owner")
    cid = conversation(owner, roles)

    ask(owner, roles, cid, "Hello")

    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        [version] = db.scalars(sa.select(RunRecord.prompt_version)).all()[-1:]
    assert version is not None and version.startswith("p-")
