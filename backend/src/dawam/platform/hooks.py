"""Ports a module exposes for modules that import it to fill (README rule 5).

``workspaces`` is imported by every Workspace-scoped module, so it cannot import them
back. What it must tell them (a Workspace was archived) it calls through a port the
composition root fills.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy.orm import Session


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
        ...
