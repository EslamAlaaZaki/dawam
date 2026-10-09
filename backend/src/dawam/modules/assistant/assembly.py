"""Builds the ``AssistantService`` with all the services its tools call.

Shared by the HTTP API (a chat) and the worker (an assistant job, which runs the same agent
loop), so both assemble the assistant the same way.
"""

from __future__ import annotations

import sqlalchemy as sa

from dawam.modules.auth import AuthService
from dawam.modules.changesets import ChangeSetService, ObjectHandlers
from dawam.modules.files import DocumentSearchService, FileService
from dawam.modules.jobs import JobRunner, JobService
from dawam.modules.kpis import KpiService
from dawam.modules.lineage import LineageService
from dawam.modules.llm import AdapterFactory, ProviderService, RoleService, WorkspaceAiService
from dawam.modules.notifications import NotificationService
from dawam.modules.sources import (
    PiiService,
    ProfilingService,
    SnapshotService,
    SourceQueryService,
    SourceSystemService,
)
from dawam.modules.warehouse import (
    DataWarehouseService,
    MappingService,
    ScoreService,
    ValidationService,
)
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.storage import FileStorage

from .internal.tools import ToolRegistry, ToolServices
from .service import AssistantService, readable_conversations


def build_assistant_service(
    engine: sa.Engine,
    settings: Settings,
    clock: Clock,
    *,
    jobs: JobRunner,
    storage: FileStorage,
    document_search: DocumentSearchService,
    change_set_handlers: ObjectHandlers,
    llm_adapters: AdapterFactory | None = None,
) -> AssistantService:
    workspaces = WorkspaceService(engine, clock=clock)
    systems = SourceSystemService(engine, workspaces=workspaces, clock=clock)
    providers = ProviderService(
        engine,
        encryption_key=settings.encryption_key.get_secret_value(),
        clock=clock,
        **({"adapters": llm_adapters} if llm_adapters else {}),
    )
    encryption_key = settings.encryption_key.get_secret_value()
    tools = ToolRegistry(
        workspaces,
        ToolServices(
            kpis=KpiService(engine, workspaces=workspaces, systems=systems, clock=clock),
            change_sets=ChangeSetService(
                engine,
                workspaces=workspaces,
                handlers=change_set_handlers,
                notifications=NotificationService(engine, clock=clock),
                clock=clock,
                conversations=readable_conversations(engine),
            ),
            files=FileService(
                engine,
                workspaces=workspaces,
                systems=systems,
                warehouses=DataWarehouseService(engine, workspaces=workspaces, clock=clock),
                storage=storage,
                search=document_search,
                clock=clock,
                max_upload_bytes=settings.upload_max_bytes,
            ),
            snapshots=SnapshotService(
                engine,
                workspaces=workspaces,
                jobs=jobs,
                encryption_key=encryption_key,
                clock=clock,
            ),
            profiling=ProfilingService(
                engine,
                workspaces=workspaces,
                jobs=jobs,
                encryption_key=encryption_key,
                clock=clock,
            ),
            pii=PiiService(engine, workspaces=workspaces, clock=clock),
            documents=document_search,
            validation=ValidationService(
                engine,
                workspaces=workspaces,
                mappings=MappingService(engine, workspaces=workspaces, clock=clock),
            ),
            source_queries=SourceQueryService(
                engine,
                workspaces=workspaces,
                encryption_key=encryption_key,
                clock=clock,
            ),
            jobs=JobService(engine, runner=jobs, clock=clock),
            scores=ScoreService(engine, workspaces=workspaces, clock=clock),
            lineage=LineageService(engine, workspaces=workspaces),
        ),
        source_query_seconds=settings.assistant_source_query_seconds,
    )
    return AssistantService(
        engine,
        workspaces=workspaces,
        roles=RoleService(engine, providers=providers, clock=clock),
        ai=WorkspaceAiService(engine, workspaces=workspaces, clock=clock),
        tools=tools,
        clock=clock,
        max_tool_calls=settings.assistant_max_tool_calls,
        max_job_tool_calls=settings.assistant_job_max_tool_calls,
        users=AuthService(engine, settings, clock=clock).get_user,
    )
