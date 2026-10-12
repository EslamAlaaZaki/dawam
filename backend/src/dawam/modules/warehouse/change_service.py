"""Owner-only System Code and target platform changes (spec §6.7, §6.9, stories 39, 87).

``DwChangeService`` proposes each change as one Change Set (``system_code_change`` and
``platform_change`` origins); nothing changes until an owner accepts it, and accepting it is
the change (see ``dw_changes`` for the items). A pending Change Set of the same kind is
superseded by a newer one. Every method authorizes through the workspaces policy first.

``extra_plans`` are planners other modules add to a platform change (the kpis module adds the
KPI formulas, which ``warehouse`` cannot read): each gets the session, the Workspace and the
old and new platforms and returns its items, which depend on the platform item.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import ChangeSetDetail, ChangeSetService
from dawam.modules.sources import StagingSourceService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .dw_changes import (
    ManualNote,
    PlatformPlan,
    plan_platform_change,
    plan_system_code_change,
)
from .platforms import TARGET_PLATFORMS, TargetPlatform
from .tables import DataWarehouseRecord

ExtraPlan = Callable[[Session, uuid.UUID, TargetPlatform, TargetPlatform], PlatformPlan]


@dataclass(frozen=True)
class ProposedChange:
    change_set: ChangeSetDetail
    manual: list[ManualNote]
    """Stored SQL a platform change could not translate (flagged "manual")."""


def staged_system_check(engine: sa.Engine) -> Callable[[uuid.UUID, uuid.UUID], bool]:
    """The ``sources`` port: whether a Source System has Staging Tables, i.e. whether a change
    to its System Code has names to rename."""

    def has_staging(workspace_id: uuid.UUID, system_id: uuid.UUID) -> bool:
        with Session(engine) as db:
            return (
                db.scalar(
                    sa.text(
                        "select 1 from dw_tables t"
                        " join data_warehouses w on w.id = t.data_warehouse_id"
                        " join src_tables s on s.id = t.source_table_id"
                        " join src_db_schemas d on d.id = s.db_schema_id"
                        " where w.workspace_id = :w and t.layer = 'staging'"
                        " and d.source_system_id = :s limit 1"
                    ),
                    {"w": workspace_id, "s": system_id},
                )
                is not None
            )

    return has_staging


class DwChangeService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        change_sets: ChangeSetService,
        clock: Clock,
        extra_plans: Sequence[ExtraPlan] = (),
        sources: StagingSourceService | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._change_sets = change_sets
        self._clock = clock
        self._extra_plans = extra_plans
        self._sources = sources or StagingSourceService(engine)

    def propose_system_code_change(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, code: str
    ) -> ChangeSetDetail:
        """Propose changing a Source System's System Code (owners only), with the rename of
        every affected Staging Table, as a ``system_code_change`` Change Set. 404
        ``not_found`` for another Workspace's system; 422 ``invalid_change_set`` for a code that
        is not identifier-safe or is taken; 409 ``system_code_unchanged``."""
        self._workspaces.authorize(user, Action.CHANGE_SYSTEM_CODE, workspace_id)
        found = self._sources.read(workspace_id, system_id=system_id, include_removed=True)
        if not found:
            raise ApiError(404, "not_found", "Source System not found.")
        system = found[0]
        code = code.strip()
        if code == system.code:
            raise ApiError(409, "system_code_unchanged", "That is the System Code already.")
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            items = plan_system_code_change(db, warehouse=warehouse, system=system, code=code)
        return self._change_sets.propose(
            user,
            workspace_id,
            origin="system_code_change",
            scope={"source_system_id": str(system_id)},
            title=f"Change System Code {system.code} to {code}",
            items=items,
        )

    def propose_platform_change(
        self, user: User, workspace_id: uuid.UUID, target: TargetPlatform
    ) -> ProposedChange:
        """Propose moving the Data Warehouse to ``target`` (owners only) as one
        ``platform_change`` Change Set: the platform, the re-translated Staging column types
        and the transpiled stored SQL; what sqlglot cannot translate is flagged "manual".
        404 ``not_set_up``; 422 ``invalid_data_warehouse``; 409 ``platform_unchanged``."""
        self._workspaces.authorize(user, Action.CHANGE_DW_PLATFORM, workspace_id)
        if target not in TARGET_PLATFORMS:
            raise ApiError(
                422,
                "invalid_data_warehouse",
                f"Unknown target platform {target!r}.",
                {"field": "target_platform"},
            )
        systems = self._sources.read(workspace_id, include_removed=True)
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            if warehouse is None:
                raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
            current: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
            if current == target:
                raise ApiError(
                    409, "platform_unchanged", "The Data Warehouse is on that platform already."
                )
            plan = plan_platform_change(db, warehouse=warehouse, systems=systems, target=target)
            for extra in self._extra_plans:
                more = extra(db, workspace_id, current, target)
                plan.items.extend(more.items)
                plan.manual.extend(more.manual)
        detail = self._change_sets.propose(
            user,
            workspace_id,
            origin="platform_change",
            scope={"kind": "platform_change"},
            title=f"Change target platform {current} to {target}",
            items=plan.items,
        )
        return ProposedChange(detail, plan.manual)
