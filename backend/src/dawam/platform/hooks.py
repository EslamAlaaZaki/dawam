"""Ports a module exposes for modules that import it to fill (README rule 5).

``workspaces`` is imported by every Workspace-scoped module, so it cannot import them
back. What it must tell them (a Workspace was created or archived) it calls through a port the
composition root fills.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class WorkspaceArchivedHook(Protocol):
    def __call__(self, db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
        """Called inside the transaction that archives the Workspace, so the hook's
        changes are kept only if the archive is."""
        ...


@dataclass(frozen=True)
class SourceAnalysis:
    """Source Analysis progress of one Source System."""

    system_id: uuid.UUID
    name: str
    status: Literal["not_started", "in_progress", "complete"]


class SourceAnalysisProvider(Protocol):
    def __call__(self, workspace_id: uuid.UUID) -> list[SourceAnalysis]:
        """The Source Analysis progress of each Source System of the Workspace. The
        ``sources`` module imports ``workspaces``, so ``workspaces`` asks through this port."""


class WorkspaceCreatedHook(Protocol):
    def __call__(self, db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
        """Called inside the transaction that creates the Workspace (after its row and its
        owner exist), so what the hook adds is kept only if the Workspace is."""
        ...


class ReadableConversations(Protocol):
    def __call__(
        self, user_id: uuid.UUID, workspace_id: uuid.UUID, conversation_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Of ``conversation_ids``, those ``user_id`` may read (their own, or shared with the
        Workspace). ``assistant`` imports ``changesets``, so ``changesets`` asks through this
        port."""
        ...


class SnapshotCreatedHook(Protocol):
    def __call__(
        self, workspace_id: uuid.UUID, source_system_id: uuid.UUID, actor_id: uuid.UUID | None
    ) -> None:
        """Called after a new Snapshot of a Source System is committed (an extraction or a
        Schema Import), outside its transaction. ``sources`` cannot import ``warehouse``,
        which follows the Snapshot with a staging sync; a failing hook is logged and never
        fails the Snapshot."""
        ...


def notify_snapshot_created(
    hook: SnapshotCreatedHook | None,
    workspace_id: uuid.UUID,
    source_system_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """Call ``hook`` (if any) after a Snapshot was committed; whatever it raises is logged,
    since the Snapshot stands either way."""
    if hook is None:
        return
    try:
        hook(workspace_id, source_system_id, actor_id)
    except Exception:
        logger.exception(
            "following a new Snapshot failed", extra={"source_system_id": str(source_system_id)}
        )
