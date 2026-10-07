"""The Source System dashboard (spec story 63) and Source Analysis progress (story 37).

Every number is computed on read from the Source Schema (the latest Snapshot), the column
profiles, the relationships and the PII findings; nothing is stored. Any member reads it
(counts only, never a finding or a value). Source Analysis of a system is ``not_started``
before its first Snapshot, ``complete`` once every table is documented and profiled and no
PII finding or relationship waits for review, and ``in_progress`` in between.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.hooks import SourceAnalysis

from .tables import (
    ColumnProfileRecord,
    PiiFindingRecord,
    RelationshipRecord,
    SnapshotColumnRecord,
    SnapshotRecord,
    SnapshotTableRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcTableRecord,
)

AnalysisStatus = Literal["not_started", "in_progress", "complete"]


@dataclass(frozen=True)
class SourceSummary:
    system_id: uuid.UUID
    has_snapshot: bool
    table_count: int
    """Tables and views in the latest Snapshot."""
    documented_tables: int
    """Of them, those with a description."""
    documented_pct: float
    profiled_tables: int
    """Of them, those with at least one column profile."""
    profiled_pct: float
    relationships_found: int
    """Suggested or accepted relationships (rejected ones are not found)."""
    relationships_accepted: int
    relationships_to_review: int
    pii_found: int
    """Columns of the latest Snapshot with a suggested or confirmed finding."""
    pii_confirmed: int
    pii_to_review: int
    status: AnalysisStatus


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _status(summary: SourceSummary) -> AnalysisStatus:
    if not summary.has_snapshot:
        return "not_started"
    done = (
        summary.table_count > 0
        and summary.documented_tables == summary.table_count
        and summary.profiled_tables == summary.table_count
        and summary.pii_to_review == 0
        and summary.relationships_to_review == 0
    )
    return "complete" if done else "in_progress"


class SourceSummaryService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService) -> None:
        self._engine = engine
        self._workspaces = workspaces

    def summary(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> SourceSummary:
        """The dashboard numbers of one Source System (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present" or system.workspace_id != workspace_id:
                raise ApiError(404, "not_found", "Source System not found.")
            return self._summaries(db, [system_id])[system_id]

    def source_analysis(self, workspace_id: uuid.UUID) -> list[SourceAnalysis]:
        """Source Analysis progress of every Source System of the Workspace, by name. Authorizes
        nothing: the stage-progress endpoint of ``workspaces`` has (it fills the port)."""
        with Session(self._engine) as db:
            systems = db.execute(
                sa.select(SourceSystemRecord.id, SourceSystemRecord.name)
                .where(
                    SourceSystemRecord.workspace_id == workspace_id,
                    SourceSystemRecord.status == "present",
                )
                .order_by(SourceSystemRecord.name, SourceSystemRecord.id)
            ).all()
            summaries = self._summaries(db, [row.id for row in systems])
            return [SourceAnalysis(row.id, row.name, summaries[row.id].status) for row in systems]

    # -- internals ----------------------------------------------------------------------

    def _summaries(
        self, db: Session, system_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, SourceSummary]:
        ids = list(system_ids)
        latest = dict(
            db.execute(
                sa.select(SnapshotRecord.source_system_id, SnapshotRecord.id).where(
                    SnapshotRecord.source_system_id.in_(ids), SnapshotRecord.is_latest
                )
            ).all()
        )
        snapshots = list(latest.values())
        system_of = {snapshot: system for system, snapshot in latest.items()}

        tables: dict[uuid.UUID, tuple[int, int, int]] = {}
        profiled = (
            sa.select(SrcColumnRecord.table_id)
            .join(ColumnProfileRecord, ColumnProfileRecord.src_column_id == SrcColumnRecord.id)
            .where(SrcColumnRecord.table_id == SnapshotTableRecord.src_table_id)
            .exists()
        )
        documented = sa.func.coalesce(sa.func.length(sa.func.trim(SrcTableRecord.description)), 0)
        for snapshot_id, total, docs, profs in db.execute(
            sa.select(
                SnapshotTableRecord.snapshot_id,
                sa.func.count(),
                sa.func.count().filter(documented > 0),
                sa.func.count().filter(profiled),
            )
            .join(SrcTableRecord, SrcTableRecord.id == SnapshotTableRecord.src_table_id)
            .where(SnapshotTableRecord.snapshot_id.in_(snapshots))
            .group_by(SnapshotTableRecord.snapshot_id)
        ):
            tables[system_of[snapshot_id]] = (total, docs, profs)

        pii: dict[uuid.UUID, tuple[int, int, int]] = {}
        for snapshot_id, found, confirmed, waiting in db.execute(
            sa.select(
                SnapshotColumnRecord.snapshot_id,
                sa.func.count(sa.distinct(SnapshotColumnRecord.src_column_id)),
                sa.func.count(sa.distinct(SnapshotColumnRecord.src_column_id)).filter(
                    PiiFindingRecord.status == "confirmed"
                ),
                sa.func.count(sa.distinct(SnapshotColumnRecord.src_column_id)).filter(
                    PiiFindingRecord.status == "suggested"
                ),
            )
            .join(
                PiiFindingRecord,
                PiiFindingRecord.src_column_id == SnapshotColumnRecord.src_column_id,
            )
            .where(
                SnapshotColumnRecord.snapshot_id.in_(snapshots),
                PiiFindingRecord.status.in_(("suggested", "confirmed")),
            )
            .group_by(SnapshotColumnRecord.snapshot_id)
        ):
            pii[system_of[snapshot_id]] = (found, confirmed, waiting)

        links: dict[uuid.UUID, tuple[int, int, int]] = {}
        for snapshot_id, found, accepted, waiting in db.execute(
            sa.select(
                SnapshotColumnRecord.snapshot_id,
                sa.func.count(),
                sa.func.count().filter(RelationshipRecord.status == "accepted"),
                sa.func.count().filter(RelationshipRecord.status == "suggested"),
            )
            .join(
                RelationshipRecord,
                RelationshipRecord.from_column_id == SnapshotColumnRecord.src_column_id,
            )
            .where(
                SnapshotColumnRecord.snapshot_id.in_(snapshots),
                RelationshipRecord.status.in_(("suggested", "accepted")),
            )
            .group_by(SnapshotColumnRecord.snapshot_id)
        ):
            links[system_of[snapshot_id]] = (found, accepted, waiting)

        result: dict[uuid.UUID, SourceSummary] = {}
        for system_id in ids:
            total, docs, profs = tables.get(system_id, (0, 0, 0))
            pii_found, pii_confirmed, pii_waiting = pii.get(system_id, (0, 0, 0))
            found, accepted, waiting = links.get(system_id, (0, 0, 0))
            draft = SourceSummary(
                system_id=system_id,
                has_snapshot=system_id in latest,
                table_count=total,
                documented_tables=docs,
                documented_pct=_pct(docs, total),
                profiled_tables=profs,
                profiled_pct=_pct(profs, total),
                relationships_found=found,
                relationships_accepted=accepted,
                relationships_to_review=waiting,
                pii_found=pii_found,
                pii_confirmed=pii_confirmed,
                pii_to_review=pii_waiting,
                status="not_started",
            )
            result[system_id] = SourceSummary(**{**draft.__dict__, "status": _status(draft)})
        return result
