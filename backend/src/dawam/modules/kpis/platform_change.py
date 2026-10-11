"""KPI formulas in a target platform change (spec §6.9 "Dialect", story 87).

A KPI's ``formula_sql`` is stored in the Data Warehouse's platform dialect, so a platform change
transpiles it. ``plan_kpi_transpile`` is the planner the ``warehouse`` platform change adds for
KPIs (it cannot read them): one ``kpi_formula`` item per formula that reads differently on the
new platform, depending on the platform item; a formula sqlglot cannot translate faithfully, or
whose translation is not one valid SELECT, is flagged "manual" and left as it is.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import ProposedItem
from dawam.modules.warehouse import (
    PLATFORM_KEY,
    ManualNote,
    PlatformPlan,
    TargetPlatform,
    manual_item,
    transpile_sql,
)
from dawam.platform.errors import ApiError

from .links import validate_formula
from .tables import KpiRecord


def plan_kpi_transpile(
    db: Session, workspace_id: uuid.UUID, current: TargetPlatform, target: TargetPlatform
) -> PlatformPlan:
    """The items that move the Workspace's KPI formulas from ``current`` to ``target``."""
    plan = PlatformPlan()
    kpis = db.scalars(
        sa.select(KpiRecord)
        .where(KpiRecord.workspace_id == workspace_id, KpiRecord.formula_sql.is_not(None))
        .order_by(KpiRecord.name)
    )
    for kpi in kpis:
        sql = kpi.formula_sql or ""
        if not sql.strip():
            continue
        result = transpile_sql("query", sql, current, target)
        reason = result.manual
        if result.sql is not None:
            try:
                validate_formula(None, result.sql)
            except ApiError as exc:
                reason = exc.message
        if reason is not None:
            note = ManualNote("kpi", kpi.id, kpi.name, reason)
            plan.manual.append(note)
            plan.items.append(
                manual_item(note, sql, key=f"mk:{kpi.id}", depends_on=(PLATFORM_KEY,))
            )
        elif result.changed and result.sql is not None:
            plan.items.append(
                ProposedItem(
                    key=f"kf:{kpi.id}",
                    object_type="kpi_formula",
                    operation="update",
                    object_id=kpi.id,
                    label=kpi.name,
                    payload={"formula_sql": result.sql},
                    depends_on=(PLATFORM_KEY,),
                )
            )
    return plan
