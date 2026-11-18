# ruff: noqa: F811
"""What each model call records (#631): the feature and job it was made for, how long it took
and whether it failed, on its ``llm_usage`` row and its span. Calls go through the metered
gateway with the scripted ``fake_llm`` behind every provider; no real model is called."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import Session

from dawam.modules.jobs.tables import JobRecord
from dawam.modules.llm import (
    LlmError,
    Reply,
    ai_call_context,
    default_ai_feature,
    in_ai_call_context,
)
from dawam.modules.llm.gateway import AUTH, UNAVAILABLE
from dawam.modules.llm.tables import UsageRecord
from dawam.platform.tracing import Tracing, configure_tracing, set_tracing
from tests.llm.test_model_fallback import backup, text  # noqa: F401
from tests.llm.test_roles_budgets import ASK, register, role_service, setup  # noqa: F401


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    settings = SimpleNamespace(
        otel_endpoint=None, otel_capture_content=False, otel_service_name="dawam-test"
    )
    configure_tracing(settings, exporter=exporter)  # type: ignore[arg-type]
    yield exporter
    set_tracing(Tracing())


def usage_rows(app) -> list[UsageRecord]:
    with Session(app.state.engine) as db:
        return list(db.scalars(sa.select(UsageRecord).order_by(UsageRecord.created_at)))


def a_job(app, workspace_id) -> uuid.UUID:
    job_id = uuid.uuid4()
    with Session(app.state.engine) as db, db.begin():
        db.add(
            JobRecord(
                id=job_id,
                workspace_id=workspace_id,
                type="pii_ai",
                title="AI PII detection: crm",
                params={},
                status="running",
                progress=0,
                log="",
                created_at=datetime(2026, 1, 5, tzinfo=UTC),
            )
        )
    return job_id


def test_a_call_without_a_context_is_recorded_as_unknown(app, role_service, setup, fake_llm):
    fake_llm.script(Reply(text="hi"))

    text(role_service.gateway_for_role("agent", workspace_id=setup))

    [row] = usage_rows(app)
    assert (row.feature, row.job_id, row.status, row.error_code) == ("unknown", None, "ok", None)
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_a_call_carries_the_feature_and_job_of_its_context(app, role_service, setup, fake_llm):
    job_id = a_job(app, setup)
    fake_llm.script(Reply(text="hi"))

    with ai_call_context("pii_scan", job_id=job_id):
        gateway = role_service.gateway_for_role("agent", workspace_id=setup)
    with ai_call_context("pii_scan", job_id=job_id):
        text(gateway)  # the context of the call counts, not where the gateway was made

    [row] = usage_rows(app)
    assert (row.feature, row.job_id) == ("pii_scan", job_id)


def test_a_failed_call_records_failed_and_its_error_code(app, role_service, setup, fake_llm):
    fake_llm.script(*[LlmError(AUTH, "bad key")] * 4)

    with ai_call_context("relationships"), pytest.raises(LlmError):
        text(role_service.gateway_for_role("agent", workspace_id=setup))

    rows = usage_rows(app)
    assert rows and all((r.status, r.error_code) == ("failed", AUTH) for r in rows)
    assert {r.feature for r in rows} == {"relationships"}


def test_a_fallback_writes_one_row_per_model(app, role_service, setup, backup, fake_llm):
    fake_llm.script(LlmError(UNAVAILABLE, "down", retry_after=0), Reply(text="from backup"))

    with ai_call_context("catalog_suggestions"):
        assert text(role_service.gateway_for_role("agent", workspace_id=setup)) == "from backup"

    rows = usage_rows(app)
    assert sorted((r.model_name, r.status, r.error_code) for r in rows) == [
        ("backup", "ok", None),
        ("big-model", "failed", UNAVAILABLE),
    ]
    assert {r.feature for r in rows} == {"catalog_suggestions"}


def test_embed_and_rerank_are_recorded_and_traced(
    app, admin_client, role_service, setup, fake_llm, spans
):
    job_id = a_job(app, setup)
    embedder = register(admin_client, "embedder", roles=("embedding",))
    reranker = register(admin_client, "reranker", roles=("rerank",))
    fake_llm.script_rerank(LlmError(UNAVAILABLE, "down"), [0.1, 0.9])
    embed = role_service.metered_gateway(uuid.UUID(embedder["id"]), "embedding", workspace_id=setup)
    rerank = role_service.metered_gateway(uuid.UUID(reranker["id"]), "rerank", workspace_id=setup)

    with ai_call_context("file_indexing", job_id=job_id):
        embed.embed(["a passage"])
    with ai_call_context("search"):
        assert rerank.rerank("q", ["a", "b"]) == [0.1, 0.9]

    rows = {(r.model_name, r.status) for r in usage_rows(app) if r.feature != "unknown"}
    assert ("embedder", "ok") in rows and ("reranker", "ok") in rows
    by_name = {s.name: s.attributes for s in spans.get_finished_spans()}
    assert by_name["embeddings embedder"]["dawam.feature"] == "file_indexing"
    assert by_name["embeddings embedder"]["dawam.job_id"] == str(job_id)
    assert by_name["rerank reranker"]["dawam.feature"] == "search"
    assert "dawam.job_id" not in by_name["rerank reranker"]


def test_a_chat_span_carries_the_feature(role_service, setup, fake_llm, spans):
    fake_llm.script(Reply(text="hi"))

    with ai_call_context("kpi_suggestions"):
        text(role_service.gateway_for_role("agent", workspace_id=setup))

    [chat] = [s for s in spans.get_finished_spans() if s.name == "chat big-model"]
    assert chat.attributes["dawam.feature"] == "kpi_suggestions"


def test_an_outer_context_wins_over_a_default_feature(app, role_service, setup, fake_llm):
    fake_llm.script(Reply(text="one"), Reply(text="two"))
    gateway = role_service.gateway_for_role("agent", workspace_id=setup)

    with ai_call_context("assistant"), default_ai_feature("search"):
        text(gateway)
    with default_ai_feature("search"):
        text(gateway)

    assert sorted(r.feature for r in usage_rows(app)) == ["assistant", "search"]


def test_a_stream_is_stepped_in_its_feature_even_when_abandoned(app, role_service, setup, fake_llm):
    fake_llm.script(Reply(text="streamed"), Reply(text="dropped"))
    gateway = role_service.gateway_for_role("agent", workspace_id=setup)

    def answer() -> Iterator[object]:
        yield from gateway.chat(ASK)
        yield from gateway.chat(ASK)

    stream = in_ai_call_context("assistant", answer())
    next(stream)  # the first call is open
    stream.close()  # abandoned: the open call is still recorded, and did not fail

    assert [(r.feature, r.status) for r in usage_rows(app)] == [("assistant", "ok")]


def test_a_call_whose_usage_cannot_be_recorded_still_answers_or_fails_as_itself(
    role_service, setup, fake_llm, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(role_service, "record_usage", broken)
    fake_llm.script(Reply(text="hi"), *[LlmError(AUTH, "bad key")] * 4)
    gateway = role_service.gateway_for_role("agent", workspace_id=setup)

    assert text(gateway) == "hi"
    with pytest.raises(LlmError) as raised:
        text(role_service.gateway_for_role("agent", workspace_id=setup))
    assert raised.value.code == AUTH
