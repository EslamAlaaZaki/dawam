"""Ports a module exposes for modules that import it to fill (README rule 5).

``workspaces`` is imported by every Workspace-scoped module, so it cannot import them
back. What it must tell them (a Workspace was created or archived) it calls through a port the
composition root fills.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session


class WorkspaceArchivedHook(Protocol):
    def __call__(self, db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
        """Called inside the transaction that archives the Workspace, so the hook's
        changes are kept only if the archive is."""
        ...


class WorkspaceCreatedHook(Protocol):
    def __call__(self, db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
        """Called inside the transaction that creates the Workspace (after its row and its
        owner exist), so what the hook adds is kept only if the Workspace is."""
        ...
