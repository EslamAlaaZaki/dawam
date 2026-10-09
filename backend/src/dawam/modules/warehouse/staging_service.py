"""Staging generation (spec §6.7, stories 81-85).

Once the Data Warehouse is set up, DAWAM makes the Staging Layer by software, with no AI: one
Staging Table per source base table of every Source System (a view only when an editor opted
it in), named ``stg_<system code>_<database schema>_<table>``, with the source's columns in
the target platform's types plus the audit columns, and a ``direct`` mapping (and lineage
edge) from every source column to its staging column.

Generation only adds: a Source Table that already has a Staging Table, or whose Staging Table
a user deleted (a Tombstone), is left alone (syncing changes of a later Snapshot is a Change
Set, see ``staging_sync``). Every method authorizes through the workspaces policy first. The
batch is audited and recorded in the activity feed once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.changesets import ChangeSetDetail, ChangeSetService
from dawam.modules.notifications import NotificationService
from dawam.modules.sources import StagingSourceService, StagingSystem
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .platforms import TargetPlatform
from .service import NamingRules
from .staging_naming import needs_placeholder
from .staging_rows import StagingFlag, build_staging, write_rows
from .staging_sync import plan_drop_removed, plan_sync
from .tables import (
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    TombstoneRecord,
)


@dataclass(frozen=True)
class StagingResult:
    tables_created: int
    columns_created: int
    tables_existing: int
    """Source tables that already had a Staging Table and were left as they are."""
    flags: list[StagingFlag]


class StagingService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        sources: StagingSourceService | None = None,
        change_sets: ChangeSetService | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self._change_sets = change_sets
        self._notifications = notifications or NotificationService(engine, clock=clock)
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._sources = sources or StagingSourceService(engine)

    def generate(self, user: User, workspace_id: uuid.UUID) -> StagingResult:
        """Generate the Staging Tables not yet made (owners and editors). 404 ``not_set_up``
        before the Data Warehouse exists."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            if warehouse is None:
                raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
            staged = {
                source_id
                for (source_id,) in db.execute(
                    sa.select(DwTableRecord.source_table_id).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                        DwTableRecord.source_table_id.is_not(None),
                    )
                )
            }
            tombstoned = set(
                db.scalars(
                    sa.select(TombstoneRecord.src_object_id).where(
                        TombstoneRecord.data_warehouse_id == warehouse.id,
                        TombstoneRecord.object_type == "staging_table",
                        TombstoneRecord.src_object_id.is_not(None),
                    )
                )
            )
            taken = set(
                db.scalars(
                    sa.select(sa.func.lower(DwTableRecord.name)).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                    )
                )
            )
            platform: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
            rules = NamingRules(**warehouse.naming_rules)
            warehouse_id = warehouse.id
        systems, existing = self._read(workspace_id, staged, tombstoned)
        try:
            with Session(self._engine) as db, db.begin():
                result = self._insert(
                    db,
                    user,
                    workspace_id,
                    warehouse_id,
                    platform,
                    (rules.load_ts_column, rules.source_system_column),
                    systems,
                    taken,
                    existing,
                )
        except IntegrityError:
            raise ApiError(
                409,
                "conflict",
                "Staging was generated at the same time by someone else. Try again.",
            ) from None
        return result

    def sync(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> ChangeSetDetail | None:
        """Propose the staging changes the Source System's latest Snapshot calls for, as a
        ``sync`` Change Set (owners and editors; spec §6.7). It replaces a pending sync of
        the same Source System, touches nothing until confirmed, and alerts the owners and
        editors. ``None`` when staging is already in line (or the Data Warehouse is not set up).
        404 ``not_found`` for another Workspace's Source System."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        found = self._sources.read(workspace_id, system_id=system_id, include_removed=True)
        if not found:
            raise ApiError(404, "not_found", "Source System not found.")
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            if warehouse is None:
                return None
            db.expunge(warehouse)
        system = self._with_placeholders(workspace_id, system_id, warehouse, found[0])
        with Session(self._engine) as db:
            taken = {
                n.lower()
                for n in db.scalars(
                    sa.select(DwTableRecord.name).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                    )
                )
            }
            items = plan_sync(
                db, warehouse=warehouse, system=system, taken=taken, now=self._clock()
            )
        if not items:
            return None
        detail = self._propose(
            user,
            workspace_id,
            origin="sync",
            scope={"source_system_id": str(system_id)},
            title=f"Sync staging with {system.code}",
            items=items,
        )
        creates = sum(1 for i in items if i.operation == "create")
        self._notifications.notify(
            self._workspaces.reviewer_ids(workspace_id),
            kind="sync_alert",
            message=(
                f"A new Snapshot of {system.code} changes staging: {len(items)} proposed "
                f"change(s), {creates} new. Review the sync Change Set."
            ),
            workspace_id=workspace_id,
            ref_type="change_set",
            ref_id=detail.change_set.id,
        )
        return detail

    def drop_removed(self, user: User, workspace_id: uuid.UUID) -> ChangeSetDetail | None:
        """Propose deleting the ``source_removed`` staging tables and columns nothing
        downstream reads, as a Change Set whose items only an owner can accept (owners and
        editors may propose it). ``None`` when there is nothing to drop. 404 ``not_set_up``
        before the Data Warehouse exists."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            if warehouse is None:
                raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
            items = plan_drop_removed(db, warehouse)
        if not items:
            return None
        return self._propose(
            user,
            workspace_id,
            origin="sync",
            scope={"kind": "drop_removed"},
            title="Drop removed staging objects",
            items=items,
        )

    def _propose(self, user: User, workspace_id: uuid.UUID, **proposal: Any) -> ChangeSetDetail:
        if self._change_sets is None:  # pragma: no cover - a programming error
            raise RuntimeError("StagingService needs a ChangeSetService to propose")
        return self._change_sets.propose(user, workspace_id, **proposal)

    def _with_placeholders(
        self,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        warehouse: DataWarehouseRecord,
        system: StagingSystem,
    ) -> StagingSystem:
        """``system`` with placeholder numbers given to the non-Latin names it will stage."""
        with Session(self._engine) as db:
            staged = set(
                db.scalars(
                    sa.select(DwTableRecord.source_table_id).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                        DwTableRecord.source_table_id.is_not(None),
                    )
                )
            )
            staged_columns = set(
                db.scalars(
                    sa.select(DwColumnRecord.source_column_id)
                    .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
                    .where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwColumnRecord.source_column_id.is_not(None),
                    )
                )
            )
        tables = [
            t.id
            for t in system.tables
            if t.status == "present"
            and t.id not in staged
            and t.placeholder_no is None
            and needs_placeholder(t.name)
        ]
        columns = [
            c.id
            for t in system.tables
            if t.status == "present"
            for c in t.columns
            if c.status == "present"
            and c.id not in staged_columns
            and c.placeholder_no is None
            and needs_placeholder(c.name)
        ]
        if not tables and not columns:
            return system
        self._sources.assign_placeholders(workspace_id, tables=tables, columns=columns)
        return self._sources.read(workspace_id, system_id=system_id, include_removed=True)[0]

    def _read(
        self, workspace_id: uuid.UUID, staged: set[uuid.UUID], tombstoned: set[uuid.UUID]
    ) -> tuple[list[StagingSystem], int]:
        """The tables still to stage (with placeholder numbers given to their non-Latin
        names; never one a user deleted: its Tombstone stays) and how many source tables
        already had a Staging Table."""
        found = self._sources.read(workspace_id)
        existing = sum(1 for s in found for t in s.tables if t.id in staged)
        skip = staged | tombstoned
        systems = self._pending(found, skip)
        tables = [
            t.id
            for s in systems
            for t in s.tables
            if t.placeholder_no is None and needs_placeholder(t.name)
        ]
        columns = [
            c.id
            for s in systems
            for t in s.tables
            for c in t.columns
            if c.placeholder_no is None and needs_placeholder(c.name)
        ]
        if tables or columns:
            self._sources.assign_placeholders(workspace_id, tables=tables, columns=columns)
            systems = self._pending(self._sources.read(workspace_id), skip)
        return systems, existing

    @staticmethod
    def _pending(systems: list[StagingSystem], staged: set[uuid.UUID]) -> list[StagingSystem]:
        return [
            StagingSystem(s.id, s.code, s.engine, [t for t in s.tables if t.id not in staged])
            for s in systems
        ]

    def _insert(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        platform: TargetPlatform,
        audit_names: tuple[str, str],
        systems: list[StagingSystem],
        taken: set[str],
        existing: int,
    ) -> StagingResult:
        now = self._clock()
        rows, flags = build_staging(
            user.id, warehouse_id, platform, audit_names, systems, taken, now
        )
        write_rows(db, rows)
        if rows.tables:
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="data_warehouse",
                entity_id=warehouse_id,
                old=None,
                new={
                    "staging_generated": {
                        "tables": len(rows.tables),
                        "columns": len(rows.columns),
                        "flagged": len(flags),
                    }
                },
                at=now,
            )
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="staging.generated",
                object_type="data_warehouse",
                object_id=warehouse_id,
                object_label="Staging Layer",
                details={"tables": len(rows.tables), "columns": len(rows.columns)},
                at=now,
            )
        return StagingResult(len(rows.tables), len(rows.columns), existing, flags)
