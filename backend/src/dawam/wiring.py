"""Composition-root pieces shared by the API (``dawam.app``) and the worker.

Modules never import each other's internals, so what one module must tell another is wired
here: the Change Set object handlers every module that owns changeable objects registers, and
what follows a new Snapshot (a staging sync, which ``sources`` cannot call because
``warehouse`` imports it) and what follows a structural change (its propagation, which
``warehouse`` and ``changesets`` cannot call because ``propagation`` imports them).
"""

from __future__ import annotations

import logging
import uuid

import sqlalchemy as sa

from dawam.modules.auth import AuthService, User
from dawam.modules.changesets import (
    AppliedChangeSet,
    ChangeSetAppliedHook,
    ChangeSetService,
    ObjectHandlers,
)
from dawam.modules.files import DocumentSearchService, FileContentHandler
from dawam.modules.kpis import KpiLinkHandler, KpiPropagationHandler
from dawam.modules.memory import MemoryItemHandler
from dawam.modules.notifications import NotificationService
from dawam.modules.propagation import PropagationService
from dawam.modules.sources import (
    SourceEnhancementHandler,
    SourceRelationshipHandler,
    SourceSystemCodeHandler,
)
from dawam.modules.warehouse import (
    ColumnMappingHandler,
    DataWarehouseHandler,
    DwColumnHandler,
    DwTableHandler,
    ManualReviewHandler,
    MappingBranchHandler,
    MappingDraftHandler,
    RegenerationOffers,
    StagingColumnHandler,
    StagingService,
    StagingTableHandler,
    TranspiledMappingHandler,
)
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.hooks import SnapshotCreatedHook
from dawam.platform.storage import create_storage


def change_set_handlers(settings: Settings | None = None) -> ObjectHandlers:
    """One handler per object type a Change Set may change. File handling needs the storage
    ``settings`` name; the callers that only propose (propagation, staging sync) leave it out."""
    files = (
        [FileContentHandler(create_storage(settings), max_bytes=settings.upload_max_bytes)]
        if settings is not None
        else []
    )
    return ObjectHandlers(
        SourceEnhancementHandler("source_table"),
        SourceEnhancementHandler("source_column"),
        SourceRelationshipHandler(),
        StagingTableHandler(),
        StagingColumnHandler(),
        DwTableHandler(),
        DwColumnHandler(),
        KpiLinkHandler(),
        ColumnMappingHandler(),
        MappingBranchHandler(),
        MappingDraftHandler(),
        KpiPropagationHandler(),
        SourceSystemCodeHandler(),
        DataWarehouseHandler(),
        TranspiledMappingHandler(),
        ManualReviewHandler(),
        MemoryItemHandler(),
        *files,
    )


def after_change_set(
    propagation_hook: ChangeSetAppliedHook, documents: DocumentSearchService
) -> ChangeSetAppliedHook:
    """What follows an accepted Change Set: its propagation, and a re-index of every file whose
    text it changed (files cannot call the search index from inside the accept's transaction)."""

    def hook(user: User, applied: AppliedChangeSet) -> None:
        for change in applied.changes:
            if change.entity_type == "file":
                try:
                    documents.queue_index(applied.workspace_id, uuid.UUID(str(change.entity_id)))
                except Exception:  # the save stands; a re-index repairs the search index
                    logging.getLogger(__name__).exception("indexing document failed")
        propagation_hook(user, applied)

    return hook


def propagation(engine: sa.Engine, clock: Clock) -> PropagationService:
    """The service that follows every structural change with its propagation (its
    ``after_edit`` and ``after_change_set`` are the warehouse's and the changesets' hooks)."""
    workspaces = WorkspaceService(engine, clock=clock)
    notifications = NotificationService(engine, clock=clock)
    return PropagationService(
        engine,
        workspaces=workspaces,
        notifications=notifications,
        change_sets=ChangeSetService(
            engine,
            workspaces=workspaces,
            handlers=change_set_handlers(),
            notifications=notifications,
            clock=clock,
        ),
    )


def staging_sync_after_snapshot(
    engine: sa.Engine, settings: Settings, clock: Clock
) -> SnapshotCreatedHook:
    """The hook that follows every new Snapshot with a staging sync Change Set, then offers
    to regenerate the DW Schema (a new Snapshot is a mandatory input of the generator). It
    acts as the user who took the Snapshot, or as an owner when nobody did."""
    workspaces = WorkspaceService(engine, clock=clock)
    notifications = NotificationService(engine, clock=clock)
    auth = AuthService(engine, settings, clock=clock)

    def hook(
        workspace_id: uuid.UUID, source_system_id: uuid.UUID, actor_id: uuid.UUID | None
    ) -> None:
        candidates = [actor_id] if actor_id else []
        candidates += workspaces.owner_ids(workspace_id)
        staging = StagingService(
            engine,
            workspaces=workspaces,
            clock=clock,
            notifications=notifications,
            change_sets=ChangeSetService(
                engine,
                workspaces=workspaces,
                handlers=change_set_handlers(),
                notifications=notifications,
                clock=clock,
            ),
        )
        for candidate in candidates:
            user = auth.get_user(candidate)
            if user is not None and user.is_active:
                staging.sync(user, workspace_id, source_system_id)
                break
        RegenerationOffers(engine, workspaces=workspaces, clock=clock).after_snapshot(workspace_id)

    return hook
