from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.auth import User
from dawam.modules.files import FileRef, purge_system_files, system_files
from dawam.modules.kpis import KpiRef, kpis_linked_to, release_system_kpis, system_kpis
from dawam.modules.sources import (
    SourceSystem,
    deleted_source_system,
    deleted_source_systems,
    purge_source_system,
    source_system_contents,
)
from dawam.modules.warehouse import (
    BrokenBranch,
    DeletedDwTable,
    DwTableRef,
    deleted_dw_tables,
    dw_table_deletion,
    purge_dw_table,
    purge_staging,
    staging_deletion,
)
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.storage import FileStorage

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletedObjects:
    """The Workspace's "deleted" list: soft-deleted Source Systems and DW tables."""

    systems: list[SourceSystem]
    tables: list[DeletedDwTable]


@dataclass(frozen=True)
class SystemDeletion:
    """Everything the permanent delete of a soft-deleted Source System takes with it, and
    what it changes elsewhere, for the owner to see before confirming."""

    system: SourceSystem
    db_schema_count: int
    table_count: int
    column_count: int
    routine_count: int
    snapshot_count: int
    staging_tables: list[DwTableRef]
    """Its Staging Tables; ``has_mapping`` tells which have a staging mapping."""
    broken_branches: list[BrokenBranch]
    """Core branches reading those Staging Tables: they stay, and become broken."""
    files: list[FileRef]
    source_link_count: int
    rehomed_kpis: list[KpiRef]
    """Linked to DW columns: moved to the Data Warehouse."""
    deleted_kpis: list[KpiRef]
    """With no DW links: deleted with it."""


@dataclass(frozen=True)
class TableDeletion:
    """What the permanent delete of a soft-deleted DW table takes with it."""

    table: DeletedDwTable
    column_names: list[str]
    has_mapping: bool
    broken_branches: list[BrokenBranch]
    """Branches of the Layer above that read it: they stay, broken."""
    unlinked_kpis: list[KpiRef]
    """KPIs that lose their links to its columns."""


def _system_not_found() -> ApiError:
    return ApiError(404, "not_found", "Deleted Source System not found.")


def _table_not_found() -> ApiError:
    return ApiError(404, "not_found", "Deleted table not found.")


class DeletionService:
    """The "deleted" list and permanent deletes (spec §7 "Soft deletes"). Soft deletes
    themselves are ``SourceSystemService.delete`` and ``ModelService.delete_table``. Every
    method authorizes through the workspaces policy first; a permanent delete is one
    transaction across the modules it touches."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        storage: FileStorage,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._storage = storage
        self._clock = clock

    def list(self, user: User, workspace_id: uuid.UUID) -> DeletedObjects:
        """The soft-deleted Source Systems and DW tables (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return DeletedObjects(
                systems=deleted_source_systems(db, workspace_id),
                tables=deleted_dw_tables(db, workspace_id),
            )

    def system_deletion(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SystemDeletion:
        """What deleting a soft-deleted Source System for good takes with it (owners only).
        404 ``not_found`` unless it is a soft-deleted system of the Workspace."""
        self._workspaces.authorize(user, Action.DELETE_OBJECTS, workspace_id)
        with Session(self._engine) as db:
            return self._system_deletion(db, workspace_id, system_id)

    def purge_system(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> None:
        """Delete a soft-deleted Source System for good (owners only): its Source Objects,
        Snapshots, Staging Tables, staging mappings, documents and files go; its KPIs linked
        to DW columns move to the Data Warehouse and the rest go; branches that read its
        Staging Tables stay, broken. Its System Code is free again."""
        self._workspaces.authorize(user, Action.DELETE_OBJECTS, workspace_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            system = deleted_source_system(db, workspace_id, system_id, lock=True)
            if system is None:
                raise _system_not_found()
            contents = source_system_contents(db, system_id)
            release_system_kpis(db, workspace_id, system_id, actor_id=user.id, at=now)
            purge_staging(db, workspace_id, contents.table_ids, actor_id=user.id, at=now)
            keys = purge_system_files(
                db, workspace_id, system_id, [*contents.table_ids, *contents.column_ids]
            )
            purge_source_system(db, system_id)
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="source_system.purged",
                object_type="source_system",
                object_id=system.id,
                object_label=system.name,
                details={"code": system.code},
                at=now,
            )
        for key in keys:
            try:
                self._storage.delete(key)
            except Exception:  # the rows are gone; an orphaned blob harms no one
                _log.warning("could not delete stored file %s", key, exc_info=True)

    def table_deletion(
        self, user: User, workspace_id: uuid.UUID, table_id: uuid.UUID
    ) -> TableDeletion:
        """What deleting a soft-deleted DW table for good takes with it (owners only).
        404 ``not_found`` unless it is a soft-deleted table of the Workspace."""
        self._workspaces.authorize(user, Action.DELETE_OBJECTS, workspace_id)
        with Session(self._engine) as db:
            found = dw_table_deletion(db, workspace_id, table_id)
            if found is None:
                raise _table_not_found()
            return TableDeletion(
                table=found.table,
                column_names=found.column_names,
                has_mapping=found.has_mapping,
                broken_branches=found.broken_branches,
                unlinked_kpis=kpis_linked_to(db, workspace_id, found.column_ids),
            )

    def purge_table(self, user: User, workspace_id: uuid.UUID, table_id: uuid.UUID) -> None:
        """Delete a soft-deleted DW table for good, with its columns and mapping (owners
        only). Its Tombstone stays; branches that read it stay, broken."""
        self._workspaces.authorize(user, Action.DELETE_OBJECTS, workspace_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            table = purge_dw_table(db, workspace_id, table_id, actor_id=user.id, at=now)
            if table is None:
                raise _table_not_found()
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="dw_table.purged",
                object_type="dw_table",
                object_id=table.id,
                object_label=table.name,
                details={"layer": table.layer},
                at=now,
            )

    def _system_deletion(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SystemDeletion:
        system = deleted_source_system(db, workspace_id, system_id)
        if system is None:
            raise _system_not_found()
        contents = source_system_contents(db, system_id)
        staging = staging_deletion(db, workspace_id, contents.table_ids)
        files = system_files(db, workspace_id, system_id)
        kpis = system_kpis(db, workspace_id, system_id)
        return SystemDeletion(
            system=system,
            db_schema_count=contents.db_schema_count,
            table_count=contents.table_count,
            column_count=contents.column_count,
            routine_count=contents.routine_count,
            snapshot_count=contents.snapshot_count,
            staging_tables=staging.staging_tables,
            broken_branches=staging.broken_branches,
            files=files.files,
            source_link_count=files.source_link_count,
            rehomed_kpis=kpis.rehomed,
            deleted_kpis=kpis.deleted,
        )
