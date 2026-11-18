# ruff: noqa: F811
"""Each feature's model calls carry its ``feature`` and, from a background job, the job's id
(#631): the job runner sets them from the job's type and id, the assistant around a chat
answer and document search around a search. The model is the scripted fake provider."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.job_handlers import AI_FEATURES, with_ai_call_context
from dawam.modules.assistant import ASSISTANT_JOB, KPI_SUGGEST_JOB
from dawam.modules.files import INDEX_JOB, REINDEX_JOB, DocumentSearchService
from dawam.modules.jobs.tables import JobRecord
from dawam.modules.llm import FakeAdapter, Reply
from dawam.modules.llm.call_context import CallContext, ai_call_context, current_call_context
from dawam.modules.llm.tables import UsageRecord
from dawam.modules.sources import CATALOG_SUGGEST_JOB, INFER_JOB, PII_AI_JOB, PII_SCAN_JOB
from tests.assistant import test_kpi_suggest_job as kpi_jobs
from tests.assistant.test_chat import ask, conversation, done, model  # noqa: F401
from tests.roles import RoleClients

settings = kpi_jobs.settings


def usage(app) -> list[tuple[str, uuid.UUID | None]]:
    with Session(app.state.engine) as db:
        return [tuple(r) for r in db.execute(sa.select(UsageRecord.feature, UsageRecord.job_id))]


def test_every_ai_job_type_has_its_feature():
    assert dict(AI_FEATURES) == {
        ASSISTANT_JOB: "assistant",
        KPI_SUGGEST_JOB: "kpi_suggestions",
        CATALOG_SUGGEST_JOB: "catalog_suggestions",
        PII_SCAN_JOB: "pii_scan",
        PII_AI_JOB: "pii_scan",
        INFER_JOB: "relationships",
        INDEX_JOB: "file_indexing",
        REINDEX_JOB: "file_indexing",
    }


def test_a_job_handler_runs_in_its_feature_and_job():
    seen: list[CallContext] = []
    job_id = uuid.uuid4()

    def handler(params: Mapping[str, Any], ctx: Any) -> None:
        seen.append(current_call_context())

    for job_type, feature in [*AI_FEATURES.items(), ("export", "unknown")]:
        with_ai_call_context(job_type, handler)({}, SimpleNamespace(job_id=job_id))
        assert seen[-1] == CallContext(feature, job_id)
    assert current_call_context() == CallContext()  # nothing leaks out of the job


def test_a_kpi_suggestion_job_records_its_feature_and_job(
    app, roles: RoleClients, model, fake_llm: FakeAdapter
):
    system = kpi_jobs.scoped_system(roles)
    fake_llm.script(Reply(text="Nothing to suggest."))

    kpi_jobs.run(roles, system)

    with Session(app.state.engine) as db:
        job_id = db.scalar(sa.select(JobRecord.id).where(JobRecord.type == KPI_SUGGEST_JOB))
    assert usage(app) == [("kpi_suggestions", job_id)]


def test_an_assistant_answer_records_the_assistant(
    app, roles: RoleClients, model, fake_llm: FakeAdapter
):
    fake_llm.script(Reply(text="Hello."))
    owner = roles.client("owner")

    done(ask(owner, roles, conversation(owner, roles), "Hi"))

    assert {feature for feature, _ in usage(app)} == {"assistant"}
    assert {job for _, job in usage(app)} == {None}


def test_a_search_is_a_search_unless_the_assistant_or_a_job_searches():
    seen: list[str] = []
    search = SimpleNamespace(_search=lambda *a, **k: seen.append(current_call_context().feature))

    DocumentSearchService.search(search, None, None, "q")  # type: ignore[arg-type]
    with ai_call_context("catalog_suggestions"):
        DocumentSearchService.search(search, None, None, "q")  # type: ignore[arg-type]

    assert seen == ["search", "catalog_suggestions"]
