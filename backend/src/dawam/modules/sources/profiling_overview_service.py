"""The Profiling page of a Source System (spec §6.5, story 57a): every table's profiling
status and findings, and how its rows grow between Snapshots.

Both read what is stored (the latest Snapshot, the column profiles, the row estimates of
every Snapshot) with one query per concern; the rules are pure
(``internal.profiling_overview``). Any member reads them, as they read a table's profile.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError

from .internal.pii import protected_column_ids
from .internal.profiling_overview import (
    ColumnFacts,
    ColumnFinding,
    FindingKind,
    Point,
    finding_of,
    growth_of,
)
from .tables import (
    ColumnProfileRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotIndexRecord,
    SnapshotRecord,
    SnapshotTableRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcTableRecord,
)


@dataclass(frozen=True)
class OverviewFinding:
    column_id: uuid.UUID
    column: str
    data_type: str
    kind: FindingKind
    null_pct: float
    row_count: int
    capped: bool
    value: str | None


@dataclass(frozen=True)
class OverviewTable:
    table_id: uuid.UUID
    db_schema: str
    name: str
    kind: str
    row_estimate: int | None
    column_count: int
    profiled_at: datetime | None
    """Its present columns' latest profile time, as the table's profile shows."""
    rows_sampled: int | None
    capped: bool
    """A column's sample hit its cap, so the table may have more rows."""
    out_of_date: bool
    """Profiled before the latest Snapshot."""
    findings: list[OverviewFinding]


@dataclass(frozen=True)
class ProfilingOverview:
    latest_snapshot_at: datetime | None
    """``None`` (and no tables) before the first Snapshot."""
    tables: list[OverviewTable]


@dataclass(frozen=True)
class GrowthPoint:
    taken_at: datetime
    row_estimate: int | None
    """``None`` where the table was not in the Snapshot or had no estimate."""


@dataclass(frozen=True)
class GrowthTable:
    table_id: uuid.UUID
    db_schema: str
    name: str
    points: list[GrowthPoint]
    first: int
    latest: int
    change_pct: float | None
    per_month: float
    in_12_months: float
    dropped_at: datetime | None


LeftOutReason = Literal["view", "no_estimate", "new", "unchanged"]


@dataclass(frozen=True)
class LeftOutTable:
    db_schema: str
    name: str
    reason: LeftOutReason


@dataclass(frozen=True)
class ProfilingGrowth:
    snapshots: list[datetime]
    """Every Snapshot's time, oldest first."""
    tables: list[GrowthTable]
    """Fastest growing first; empty with fewer than two Snapshots."""
    left_out: list[LeftOutTable]
    total_first: int
    total_latest: int
    total_per_month: float


class ProfilingOverviewService:
    """Every method authorizes through the workspaces policy."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService) -> None:
        self._engine = engine
        self._workspaces = workspaces

    def overview(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> ProfilingOverview:
        """Each table and view of the latest Snapshot: its profiling status and findings."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            latest = db.scalars(
                sa.select(SnapshotRecord).where(
                    SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
                )
            ).first()
            if latest is None:
                return ProfilingOverview(latest_snapshot_at=None, tables=[])
            sid = latest.id
            tables = db.execute(
                sa.select(
                    SnapshotTableRecord.src_table_id,
                    SnapshotTableRecord.db_schema,
                    SnapshotTableRecord.name,
                    SnapshotTableRecord.kind,
                    SnapshotTableRecord.row_estimate,
                )
                .join(SrcTableRecord, SrcTableRecord.id == SnapshotTableRecord.src_table_id)
                .where(SnapshotTableRecord.snapshot_id == sid, SrcTableRecord.status == "present")
                .order_by(SnapshotTableRecord.db_schema, SnapshotTableRecord.name)
            ).all()
            columns = db.execute(
                sa.select(
                    SnapshotColumnRecord.src_column_id,
                    SnapshotColumnRecord.src_table_id,
                    SnapshotColumnRecord.name,
                    SnapshotColumnRecord.data_type,
                    SnapshotColumnRecord.is_pk,
                )
                .join(SrcColumnRecord, SrcColumnRecord.id == SnapshotColumnRecord.src_column_id)
                .where(SnapshotColumnRecord.snapshot_id == sid, SrcColumnRecord.status == "present")
                .order_by(SnapshotColumnRecord.src_table_id, SnapshotColumnRecord.ordinal)
            ).all()
            keys = _single_column_keys(db, sid)
            # The Snapshot's present columns as a sub-select: one bind parameter, not one
            # per column.
            column_ids = (
                sa.select(SnapshotColumnRecord.src_column_id)
                .join(SrcColumnRecord, SrcColumnRecord.id == SnapshotColumnRecord.src_column_id)
                .where(SnapshotColumnRecord.snapshot_id == sid, SrcColumnRecord.status == "present")
            )
            profiles = {
                p.src_column_id: p
                for p in db.scalars(
                    sa.select(ColumnProfileRecord).where(
                        ColumnProfileRecord.src_column_id.in_(column_ids)
                    )
                )
            }
            protected = protected_column_ids(db, column_ids)

        by_table: dict[uuid.UUID, list[sa.Row]] = {}
        for column in columns:
            by_table.setdefault(column.src_table_id, []).append(column)
        result = []
        for t in tables:
            own = by_table.get(t.src_table_id, [])
            profiled = [(c, profiles[c.src_column_id]) for c in own if c.src_column_id in profiles]
            findings = []
            for c, p in profiled:
                found = finding_of(
                    ColumnFacts(
                        row_count=p.row_count,
                        null_pct=p.null_pct,
                        distinct_count=p.distinct_count,
                        row_cap=p.row_cap,
                        min=p.min,
                        is_protected=c.src_column_id in protected,
                        # A view declares no keys, so its unique columns are not flagged.
                        is_key=t.kind == "view" or c.is_pk or (t.src_table_id, c.name) in keys,
                    )
                )
                if found is not None:
                    findings.append(_finding(c, found))
            profiled_at = max((p.profiled_at for _, p in profiled), default=None)
            result.append(
                OverviewTable(
                    table_id=t.src_table_id,
                    db_schema=t.db_schema,
                    name=t.name,
                    kind=t.kind,
                    row_estimate=t.row_estimate,
                    column_count=len(own),
                    profiled_at=profiled_at,
                    rows_sampled=max((p.row_count for _, p in profiled), default=None),
                    capped=any(p.row_count >= p.row_cap for _, p in profiled),
                    out_of_date=profiled_at is not None and profiled_at < latest.taken_at,
                    findings=findings,
                )
            )
        return ProfilingOverview(latest_snapshot_at=latest.taken_at, tables=result)

    def growth(self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID) -> ProfilingGrowth:
        """How each table of the latest Snapshot grows, from its row estimate in every
        Snapshot."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            snapshots = db.execute(
                sa.select(SnapshotRecord.id, SnapshotRecord.taken_at, SnapshotRecord.is_latest)
                .where(SnapshotRecord.source_system_id == system_id)
                .order_by(SnapshotRecord.taken_at)
            ).all()
            latest = next((s.id for s in snapshots if s.is_latest), None)
            rows = (
                db.execute(
                    sa.select(
                        SnapshotTableRecord.snapshot_id,
                        SnapshotTableRecord.src_table_id,
                        SnapshotTableRecord.db_schema,
                        SnapshotTableRecord.name,
                        SnapshotTableRecord.kind,
                        SnapshotTableRecord.row_estimate,
                    ).where(SnapshotTableRecord.snapshot_id.in_([s.id for s in snapshots]))
                ).all()
                if len(snapshots) >= 2
                else []
            )

        estimates = {(r.snapshot_id, r.src_table_id): r.row_estimate for r in rows}
        current = sorted(
            (r for r in rows if r.snapshot_id == latest), key=lambda r: (r.db_schema, r.name)
        )
        tables: list[GrowthTable] = []
        left_out: list[LeftOutTable] = []
        ends: list[tuple[int, int]] = []
        for t in current:
            series = [
                GrowthPoint(s.taken_at, estimates.get((s.id, t.src_table_id))) for s in snapshots
            ]
            points = [
                Point(p.taken_at, p.row_estimate) for p in series if p.row_estimate is not None
            ]
            if t.kind != "view" and len(points) >= 2:
                # Unchanged tables count in the totals too; they add nothing per month.
                ends.append((points[0].row_estimate, points[-1].row_estimate))
            growth = None if t.kind == "view" else growth_of(points)
            if growth is None:
                reason: LeftOutReason = (
                    "view"
                    if t.kind == "view"
                    else "no_estimate"
                    if not points
                    else "new"
                    if len(points) == 1
                    else "unchanged"
                )
                left_out.append(LeftOutTable(t.db_schema, t.name, reason))
                continue
            tables.append(
                GrowthTable(
                    table_id=t.src_table_id,
                    db_schema=t.db_schema,
                    name=t.name,
                    points=series,
                    first=growth.first,
                    latest=growth.latest,
                    change_pct=growth.change_pct,
                    per_month=growth.per_month,
                    in_12_months=growth.in_12_months,
                    dropped_at=growth.dropped_at,
                )
            )
        tables.sort(key=lambda g: -g.per_month)
        return ProfilingGrowth(
            snapshots=[s.taken_at for s in snapshots],
            tables=tables,
            left_out=left_out,
            total_first=sum(first for first, _ in ends),
            total_latest=sum(latest for _, latest in ends),
            total_per_month=sum(g.per_month for g in tables),
        )

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        record = db.get(SourceSystemRecord, system_id)
        if record is None or record.status != "present" or record.workspace_id != workspace_id:
            raise ApiError(404, "not_found", "Source System not found.")
        return record


def _single_column_keys(db: Session, snapshot_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    """``(table, column)`` of each single-column primary key, unique constraint and unique
    index in the Snapshot."""
    keys = {
        (table_id, columns[0])
        for table_id, columns in db.execute(
            sa.select(
                SnapshotConstraintRecord.src_table_id, SnapshotConstraintRecord.columns
            ).where(
                SnapshotConstraintRecord.snapshot_id == snapshot_id,
                SnapshotConstraintRecord.type.in_(("pk", "unique")),
            )
        )
        if len(columns) == 1
    }
    keys |= {
        (table_id, columns[0])
        for table_id, columns in db.execute(
            sa.select(SnapshotIndexRecord.src_table_id, SnapshotIndexRecord.columns).where(
                SnapshotIndexRecord.snapshot_id == snapshot_id, SnapshotIndexRecord.is_unique
            )
        )
        if len(columns) == 1
    }
    return keys


def _finding(column: sa.Row, found: ColumnFinding) -> OverviewFinding:
    return OverviewFinding(
        column_id=column.src_column_id,
        column=column.name,
        data_type=column.data_type,
        kind=found.kind,
        null_pct=found.null_pct,
        row_count=found.row_count,
        capped=found.capped,
        value=found.value,
    )
