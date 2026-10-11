"""KPIs in change propagation (spec §6.10, §6.13).

``plan_kpi_propagation`` turns the warehouse's plan into KPI items: formula SQL respelled
for a renamed Core or Mart table or column (after the rename's item when that is optional),
and re-pointing a KPI's links (and its formula's identifiers) from a Core column to the Mart
column now mapped ``direct`` from it. KPIs whose formula names a removed table or column are
only flagged: deletes never cascade. Runs in the caller's session and authorizes nothing.

``KpiPropagationHandler`` (``kpi_formula`` items) changes a KPI like ``KpiLinkHandler``, but
checks only that a proposed formula parses: it may name a column an earlier optional item of
the same Change Set renames, and the engine accepts that item first.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import ProposedItem
from dawam.modules.warehouse import (
    Propagation,
    Renames,
    read_schema,
    rename_in_sql,
    sql_reads,
)
from dawam.platform.errors import ApiError

from .link_changes import FIELDS, KpiLinkHandler, _invalid, _sql
from .links import validate_formula
from .tables import KpiLinkRecord, KpiRecord


@dataclass
class KpiPlan:
    items: list[ProposedItem] = field(default_factory=list)
    flagged: list[uuid.UUID] = field(default_factory=list)
    """KPIs a change breaks that nothing proposed fixes (a removed input)."""


@dataclass
class _Edit:
    name: str
    payload: dict[str, Any]
    depends_on: set[str]
    why: list[str]


def plan_kpi_propagation(db: Session, workspace_id: uuid.UUID, plan: Propagation) -> KpiPlan:
    out = KpiPlan()
    if not plan.platform:
        return out
    kpis = list(
        db.scalars(
            sa.select(KpiRecord)
            .where(KpiRecord.workspace_id == workspace_id)
            .order_by(KpiRecord.name, KpiRecord.id)
        )
    )
    edits: dict[uuid.UUID, _Edit] = {}

    def edit(kpi: KpiRecord) -> _Edit:
        if kpi.id not in edits:
            edits[kpi.id] = _Edit(kpi.name, {}, set(), [])
        return edits[kpi.id]

    for rename in plan.kpi_renames:
        for kpi in kpis:
            current = edits[kpi.id].payload.get("formula_sql") if kpi.id in edits else None
            sql = current or kpi.formula_sql
            if not sql:
                continue
            renamed = rename_in_sql(sql, plan.platform, rename.renames)
            if renamed == sql:
                continue
            found = edit(kpi)
            found.payload["formula_sql"] = renamed
            found.why.append("follow the rename")
            if rename.depends_on:
                found.depends_on.add(rename.depends_on)
    for removal in plan.kpi_removals:
        for kpi in kpis:
            if kpi.formula_sql and sql_reads(kpi.formula_sql, plan.platform, removal.names):
                out.flagged.append(kpi.id)
    if plan.repoints:
        _repoint(db, workspace_id, plan, kpis, edit)
    for kpi_id, found in edits.items():
        out.items.append(
            ProposedItem(
                key=f"kpi:{kpi_id}",
                object_type=KpiPropagationHandler.object_type,
                operation="update",
                object_id=kpi_id,
                label=f"KPI {found.name}: {', '.join(dict.fromkeys(found.why))}",
                payload=found.payload,
                depends_on=sorted(found.depends_on),
            )
        )
    return out


def _repoint(db: Session, workspace_id: uuid.UUID, plan: Propagation, kpis, edit) -> None:
    """Re-point links from a Core column to the Mart column now mapped from it."""
    to_mart = {core.id: (core, mart) for core, mart in plan.repoints}
    links: dict[uuid.UUID, list[uuid.UUID]] = {}
    for kpi_id, column_id in db.execute(
        sa.select(KpiLinkRecord.kpi_id, KpiLinkRecord.dw_column_id).where(
            KpiLinkRecord.kpi_id.in_([k.id for k in kpis])
        )
    ):
        links.setdefault(kpi_id, []).append(column_id)
    schema = read_schema(db, workspace_id)
    core_schema = plan.layer_schemas.get("core")
    for kpi in kpis:
        linked = links.get(kpi.id, [])
        moving = [to_mart[c] for c in linked if c in to_mart]
        if not moving:
            continue
        found = edit(kpi)
        found.payload["links"] = sorted(
            {str(to_mart[c][1].id) if c in to_mart else str(c) for c in linked}
        )
        found.why.append("re-point to the Mart")
        sql = found.payload.get("formula_sql") or kpi.formula_sql
        if not sql:
            continue
        targets: dict[str, set[str]] = {}
        for core, mart in moving:
            targets.setdefault(core.table_name.lower(), set()).add(mart.table_name)
        if any(len(names) > 1 for names in targets.values()):
            continue  # one Core table split across Mart tables: re-point the links only
        tables = {core: next(iter(names)) for core, names in targets.items()}
        renames = Renames(
            tables=tables,
            columns={
                (core.table_name.lower(), core.name.lower()): mart.name for core, mart in moving
            },
            schema=core_schema.lower() if core_schema else None,
        )
        repointed = rename_in_sql(sql, plan.platform, renames)
        if repointed == sql:
            continue
        try:
            validate_formula(schema, repointed)
        except ApiError:
            continue  # the formula reads more of the Core table than the Mart holds
        found.payload["formula_sql"] = repointed


class KpiPropagationHandler(KpiLinkHandler):
    object_type = "kpi_formula"

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        unknown = [f for f in payload if f not in FIELDS]
        if unknown or not payload:
            raise _invalid(f"A kpi_formula item changes some of {', '.join(FIELDS)}.")
        if self._record(db, workspace_id, object_id) is None:
            raise _invalid("The KPI does not exist.")
        if "formula_sql" in payload:
            try:
                validate_formula(None, _sql(payload["formula_sql"]))
            except ApiError as exc:
                raise _invalid(exc.message) from None
