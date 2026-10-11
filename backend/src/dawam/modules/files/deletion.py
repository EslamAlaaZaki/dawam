"""The files' part of soft deletes (spec §7 "Soft deletes"), in the caller's session and
without a permission check: the ``deletions`` module authorizes and runs a permanent delete
in one transaction, then deletes the stored bytes once it has committed.

A deleted Source System's documents and files go with it: its file area (with the documents'
search chunks and table and column links), its repository and issue links (by foreign key),
and any link of another file to one of its tables or columns.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import FileObjectLinkRecord, SourceLinkRecord, WorkspaceFileRecord


@dataclass(frozen=True)
class FileRef:
    id: uuid.UUID
    path: str
    is_document: bool
    """It has extracted text (a document)."""


@dataclass(frozen=True)
class SystemFiles:
    files: list[FileRef]
    source_link_count: int
    """Repository, Jira and Confluence links of the system."""


def _area(workspace_id: uuid.UUID, system_id: uuid.UUID) -> sa.ColumnElement[bool]:
    return sa.and_(
        WorkspaceFileRecord.workspace_id == workspace_id,
        WorkspaceFileRecord.owner_kind == "source_system",
        WorkspaceFileRecord.owner_id == system_id,
    )


def system_files(db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID) -> SystemFiles:
    """The files of a Source System's file area, by name, and how many links it has."""
    files = [
        FileRef(r.id, r.path, r.text_status == "extracted")
        for r in db.scalars(
            sa.select(WorkspaceFileRecord)
            .where(_area(workspace_id, system_id))
            .order_by(WorkspaceFileRecord.path)
        )
    ]
    links = db.scalar(
        sa.select(sa.func.count()).where(SourceLinkRecord.source_system_id == system_id)
    )
    return SystemFiles(files, links or 0)


def purge_system_files(
    db: Session,
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    source_object_ids: Collection[uuid.UUID],
) -> list[str]:
    """Delete the system's file area and the document links to its tables and columns
    (``source_object_ids``); return the storage keys whose bytes the caller deletes once
    the transaction has committed."""
    keys = list(
        db.scalars(sa.select(WorkspaceFileRecord.storage_key).where(_area(workspace_id, system_id)))
    )
    if source_object_ids:
        db.execute(
            sa.delete(FileObjectLinkRecord).where(
                FileObjectLinkRecord.object_id.in_(list(source_object_ids))
            )
        )
    db.execute(sa.delete(WorkspaceFileRecord).where(_area(workspace_id, system_id)))
    return keys
