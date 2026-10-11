"""What the Data Warehouse score reads of the KPIs, and the rescore a KPI change asks for.

The warehouse module cannot import this one (it imports the warehouse), so the composition
root passes ``kpis_for_score`` to the score service, and a flush of a KPI or its links
tells the warehouse's score trigger that the Workspace's KPIs changed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.warehouse import DesignKpi, touch_score

from .tables import KpiLinkRecord, KpiRecord


def kpis_for_score(db: Session, workspace_id: uuid.UUID) -> list[DesignKpi]:
    """The Workspace's KPIs with the DW columns each links to."""
    kpis = list(
        db.execute(
            sa.select(KpiRecord.id, KpiRecord.name, KpiRecord.definition)
            .where(KpiRecord.workspace_id == workspace_id)
            .order_by(sa.func.lower(KpiRecord.name), KpiRecord.id)
        )
    )
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for kpi_id, column_id in db.execute(
        sa.select(KpiLinkRecord.kpi_id, KpiLinkRecord.dw_column_id)
        .join(KpiRecord, KpiRecord.id == KpiLinkRecord.kpi_id)
        .where(KpiRecord.workspace_id == workspace_id)
    ):
        links[kpi_id].append(column_id)
    return [DesignKpi(i, name, definition, tuple(links.get(i, ()))) for i, name, definition in kpis]


def touch_kpi_links(db: Session, kpi_id: uuid.UUID) -> None:
    """Ask for a rescore after the links of ``kpi_id`` changed (they are bulk-written)."""
    workspace_id = db.scalars(
        sa.select(KpiRecord.workspace_id).where(KpiRecord.id == kpi_id)
    ).first()
    if workspace_id is not None:
        touch_score(db, "workspace", workspace_id)


def _on_flush(session: Session, _context: Any) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, KpiRecord):
            touch_score(session, "workspace", obj.workspace_id)


sa.event.listen(Session, "after_flush", _on_flush)
