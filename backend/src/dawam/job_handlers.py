"""Every module's background-job handlers, by job type.

A composition root (README rule 4), shared by the two that run jobs: ``dawam.app``
(which needs the handlers to accept a submitted job, and runs them inline in tests) and
``dawam.worker`` (which runs queued jobs). Each ticket that adds a job type (spec §7:
``extract``, ``profile``, ``export``, ...) adds its handler here: to ``JOB_HANDLERS`` if
it needs nothing, or to ``_service_handlers`` if it is a method of a module's service
built on the app's database, settings and clock.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlalchemy as sa

from dawam.modules.files import (
    REINDEX_JOB,
    DocumentAiPolicy,
    DocumentSearchService,
    NoDocumentAi,
    RegisteredEmbeddingModels,
)
from dawam.modules.jobs import JobHandler, JobRunner, UnknownJobTypeError
from dawam.modules.llm import AdapterFactory, ProviderService, RoleService
from dawam.modules.sources import (
    EXTRACT_JOB,
    INFER_JOB,
    PII_SCAN_JOB,
    PROFILE_JOB,
    PiiScanService,
    ProfilingService,
    RelationshipService,
    SnapshotService,
)
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.wiring import staging_sync_after_snapshot

JOB_HANDLERS: Mapping[str, JobHandler] = {}


def build_document_search(
    runner: JobRunner,
    engine: sa.Engine,
    settings: Settings,
    clock: Clock,
    *,
    llm_adapters: AdapterFactory | None = None,
    document_ai: DocumentAiPolicy | None = None,
) -> DocumentSearchService:
    """The document search service, as the API and the worker build it. Until the
    Workspace AI settings exist, ``document_ai`` defaults to ``NoDocumentAi``: documents
    are searched with full-text only and never sent to a model."""
    options = {"adapters": llm_adapters} if llm_adapters is not None else {}
    providers = ProviderService(
        engine,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
        **options,
    )
    return DocumentSearchService(
        engine,
        workspaces=WorkspaceService(engine, clock=clock),
        ai=document_ai or NoDocumentAi(),
        embeddings=RegisteredEmbeddingModels(
            providers, RoleService(engine, providers=providers, clock=clock)
        ),
        jobs=runner,
        clock=clock,
    )


def _service_handlers(
    runner: JobRunner,
    engine: sa.Engine,
    settings: Settings,
    clock: Clock,
    llm_adapters: AdapterFactory | None,
    document_ai: DocumentAiPolicy | None,
) -> dict[str, JobHandler]:
    snapshots = SnapshotService(
        engine,
        workspaces=WorkspaceService(engine, clock=clock),
        jobs=runner,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
        on_snapshot=staging_sync_after_snapshot(engine, settings, clock),
    )
    profiling = ProfilingService(
        engine,
        workspaces=WorkspaceService(engine, clock=clock),
        jobs=runner,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
    )
    pii_scans = PiiScanService(
        engine,
        workspaces=WorkspaceService(engine, clock=clock),
        jobs=runner,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
    )
    relationships = RelationshipService(
        engine,
        workspaces=WorkspaceService(engine, clock=clock),
        jobs=runner,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
    )
    documents = build_document_search(
        runner, engine, settings, clock, llm_adapters=llm_adapters, document_ai=document_ai
    )
    return {
        EXTRACT_JOB: snapshots.run_extraction,
        PROFILE_JOB: profiling.run_profiling,
        PII_SCAN_JOB: pii_scans.run_scan,
        INFER_JOB: relationships.run_inference,
        REINDEX_JOB: documents.run_reindex,
    }


def register_job_handlers(
    runner: JobRunner,
    *,
    engine: sa.Engine,
    settings: Settings,
    clock: Clock,
    llm_adapters: AdapterFactory | None = None,
    document_ai: DocumentAiPolicy | None = None,
) -> None:
    """Register every handler on ``runner``. A type the runner already has is left alone:
    tests build several apps on one shared runner, and the first app's handlers serve all."""
    handlers = {
        **_service_handlers(runner, engine, settings, clock, llm_adapters, document_ai),
        **JOB_HANDLERS,
    }
    for job_type, handler in handlers.items():
        try:
            runner.handler(job_type)
        except UnknownJobTypeError:
            runner.register(job_type, handler)
