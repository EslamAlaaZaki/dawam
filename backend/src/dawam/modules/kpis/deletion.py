"""The KPIs' part of soft deletes (spec §7 "Soft deletes"), in the caller's session and
without a permission check: the ``deletions`` module authorizes and runs a permanent delete
in one transaction.

When a Source System is deleted for good, its KPIs already linked to DW columns are re-homed
to the Data Warehouse (``source_system_id = None``); its KPIs with no links go with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit

from .links import drop_links
from .service import _snapshot
from .tables import KpiLinkRecord, KpiRecord


@dataclass(frozen=True)
class KpiRef:
    id: uuid.UUID
    name: str


@dataclass(frozen=True)
class SystemKpis:
    """A Source System's KPIs, split by what its permanent delete does to them."""

    rehomed: list[KpiRef]
    """Linked to DW columns: moved to the Data Warehouse."""
    deleted: list[KpiRef]
    """With no DW links: deleted with the system."""


def _system_kpis(db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID) -> list[KpiRecord]:
    return list(
        db.scalars(
            sa.select(KpiRecord)
            .where(KpiRecord.workspace_id == workspace_id, KpiRecord.source_system_id == system_id)
            .order_by(sa.func.lower(KpiRecord.name), KpiRecord.id)
        )
    )


def _linked(db: Session, kpi_ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    if not kpi_ids:
        return set()
    return set(
        db.scalars(sa.select(KpiLinkRecord.kpi_id).where(KpiLinkRecord.kpi_id.in_(list(kpi_ids))))
    )


def system_kpis(db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID) -> SystemKpis:
    """What the permanent delete of ``system_id`` does to its KPIs."""
    records = _system_kpis(db, workspace_id, system_id)
    linked = _linked(db, [r.id for r in records])
    return SystemKpis(
        rehomed=[KpiRef(r.id, r.name) for r in records if r.id in linked],
        deleted=[KpiRef(r.id, r.name) for r in records if r.id not in linked],
    )


def kpis_linked_to(
    db: Session, workspace_id: uuid.UUID, column_ids: Collection[uuid.UUID]
) -> list[KpiRef]:
    """The Workspace's KPIs linked to any of the given DW columns: deleting the columns
    drops those links."""
    if not column_ids:
        return []
    rows = db.execute(
        sa.select(KpiRecord.id, KpiRecord.name)
        .where(
            KpiRecord.workspace_id == workspace_id,
            KpiRecord.id.in_(
                sa.select(KpiLinkRecord.kpi_id).where(
                    KpiLinkRecord.dw_column_id.in_(list(column_ids))
                )
            ),
        )
        .order_by(sa.func.lower(KpiRecord.name), KpiRecord.id)
    )
    return [KpiRef(i, n) for i, n in rows]


def release_system_kpis(
    db: Session,
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    at: datetime,
) -> SystemKpis:
    """Re-home the system's linked KPIs to the Data Warehouse and delete the rest, audited
    and recorded as activity."""
    records = _system_kpis(db, workspace_id, system_id)
    linked = _linked(db, [r.id for r in records])
    for record in records:
        if record.id in linked:
            record.source_system_id = None
            record.version += 1
            record.updated_at = at
            old: dict | None = {"source_system_id": str(system_id)}
            new: dict | None = {"source_system_id": None}
            verb = "kpi.rehomed"
        else:
            old, new, verb = _snapshot(record), None, "kpi.deleted"
            drop_links(db, record.id)
            db.delete(record)
        record_audit(
            db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            entity_type="kpi",
            entity_id=record.id,
            old=old,
            new=new,
            at=at,
        )
        record_activity(
            db,
            workspace_id=workspace_id,
            actor_id=actor_id,
            verb=verb,
            object_type="kpi",
            object_id=record.id,
            object_label=record.name,
            details={"source_system_id": str(system_id)},
            at=at,
        )
    db.flush()
    return SystemKpis(
        rehomed=[KpiRef(r.id, r.name) for r in records if r.id in linked],
        deleted=[KpiRef(r.id, r.name) for r in records if r.id not in linked],
    )
