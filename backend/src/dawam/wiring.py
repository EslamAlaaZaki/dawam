"""Composition-root pieces shared by the API (``dawam.app``) and the worker.

Modules never import each other's internals, so what one module must tell another is wired
here: the Change Set object handlers every module that owns changeable objects registers, and
what follows a new Snapshot (a staging sync, which ``sources`` cannot call because
``warehouse`` imports it).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from dawam.modules.auth import AuthService
from dawam.modules.changesets import ChangeSetService, ObjectHandlers
from dawam.modules.notifications import NotificationService
from dawam.modules.sources import SourceEnhancementHandler
from dawam.modules.warehouse import StagingColumnHandler, StagingService, StagingTableHandler
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.config import Settings
from dawam.platform.hooks import SnapshotCreatedHook


def change_set_handlers(engine: sa.Engine) -> ObjectHandlers:
    """One handler per object type a Change Set may change."""
    return ObjectHandlers(
        SourceEnhancementHandler("source_table"),
        SourceEnhancementHandler("source_column"),
        StagingTableHandler(),
        StagingColumnHandler(),
    )


def staging_sync_after_snapshot(
    engine: sa.Engine, settings: Settings, clock: Clock
) -> SnapshotCreatedHook:
    """The hook that follows every new Snapshot with a staging sync Change Set. It acts as
    the user who took the Snapshot, or as an owner when nobody did."""
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
                handlers=change_set_handlers(engine),
                notifications=notifications,
                clock=clock,
            ),
        )
        for candidate in candidates:
            user = auth.get_user(candidate)
            if user is not None and user.is_active:
                staging.sync(user, workspace_id, source_system_id)
                return

    return hook
