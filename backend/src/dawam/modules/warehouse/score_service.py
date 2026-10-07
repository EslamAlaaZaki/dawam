"""Scoring a Data Warehouse (spec §6.11, stories 114-118, 121).

``recalculate`` loads the design, runs the registered checks (``score_checks``) through the
engine (``scoring``), stores a ``ScoreRun`` summary and replaces the latest per-check
results. ``current`` and ``history`` are what members read. Recalculation after a design
change is triggered by ``score_trigger``, debounced.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .mapping_service import SYSTEM_ROLES
from .score_checks import registry as default_registry
from .scoring import (
    ALL_LAYERS,
    SEVERITY_WEIGHTS,
    CheckRegistry,
    Design,
    DesignBranch,
    DesignColumn,
    DesignTable,
    MappingFact,
    ScoreReport,
    evaluate,
    grade_of,
)
from .service import NamingRules
from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    ScoreCheckResultRecord,
    ScoreRunRecord,
    TableMappingRecord,
)

HISTORY_LIMIT = 100
KEPT_RUNS = 1000
"""Summary rows kept per Data Warehouse; the oldest go first."""
MESSAGE_MAX_LENGTH = 1000


@dataclass(frozen=True)
class LayerView:
    layer: str
    score: float | None
    grade: str | None
    table_count: int

    @property
    def scored(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class TableView:
    table_id: uuid.UUID
    layer: str
    name: str
    score: float


@dataclass(frozen=True)
class FailedCheck:
    check_code: str
    title: str
    category: str
    severity: str
    layer: str
    object_type: str
    object_id: uuid.UUID
    table_id: uuid.UUID
    object_name: str
    message: str
    fix_hint: str
    link: str
    """An app path to the object at fault."""


@dataclass(frozen=True)
class Score:
    """The latest run, as members see it."""

    score: float | None
    grade: str | None
    capped: bool
    calculated_at: datetime
    layers: list[LayerView]
    tables: list[TableView]
    failed_checks: list[FailedCheck]
    checks_passed: int
    checks_failed: int


@dataclass(frozen=True)
class ScoreRunView:
    id: uuid.UUID
    score: float | None
    grade: str | None
    layers: list[LayerView]
    created_at: datetime


def _not_set_up() -> ApiError:
    return ApiError(404, "not_found", "The Data Warehouse is not set up yet.")


def link_to(finding_table: uuid.UUID, object_type: str, object_id: uuid.UUID) -> str:
    column = f"&column={object_id}" if object_type == "column" else ""
    return f"?table={finding_table}{column}"


def _layer_views(per_layer: dict) -> list[LayerView]:
    return [
        LayerView(
            name,
            per_layer.get(name, {}).get("score"),
            per_layer.get(name, {}).get("grade"),
            per_layer.get(name, {}).get("tables", 0),
        )
        for name in ALL_LAYERS
    ]


class ScoreService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        registry: CheckRegistry = default_registry,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._registry = registry

    # --- reading ---------------------------------------------------------------------

    def current(self, user: User, workspace_id: uuid.UUID) -> Score:
        """The latest run (any member): the Data Warehouse score and grade, each Layer's
        score ("not scored" when it has no table), each table's score and the failed checks
        with severity, object link and fix hint. A Data Warehouse never scored is scored
        now. 404 ``not_found`` before set up."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse_id = self._warehouse_id(db, workspace_id)
            if warehouse_id is None:
                raise _not_set_up()
            run = self._latest_run(db, warehouse_id)
        if run is None:
            self.recalculate(warehouse_id)
            with Session(self._engine) as db:
                run = self._latest_run(db, warehouse_id)
        assert run is not None
        with Session(self._engine) as db:
            rows = list(
                db.scalars(
                    sa.select(ScoreCheckResultRecord).where(
                        ScoreCheckResultRecord.data_warehouse_id == warehouse_id
                    )
                )
            )
        return self._view(run, rows)

    def history(
        self, user: User, workspace_id: uuid.UUID, *, limit: int = HISTORY_LIMIT
    ) -> list[ScoreRunView]:
        """The score trend, oldest first (any member): one summary per run, the latest
        ``limit`` of them. 404 ``not_found`` before set up."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse_id = self._warehouse_id(db, workspace_id)
            if warehouse_id is None:
                raise _not_set_up()
            runs = list(
                db.scalars(
                    sa.select(ScoreRunRecord)
                    .where(ScoreRunRecord.data_warehouse_id == warehouse_id)
                    .order_by(ScoreRunRecord.created_at.desc(), ScoreRunRecord.id.desc())
                    .limit(limit)
                )
            )
        return [
            ScoreRunView(r.id, r.score, r.grade, _layer_views(r.per_layer), r.created_at)
            for r in reversed(runs)
        ]

    # --- recalculating ---------------------------------------------------------------

    def recalculate(
        self, data_warehouse_id: uuid.UUID, *, disabled: Collection[str] = ()
    ) -> ScoreReport | None:
        """Score the Data Warehouse now (no user: the design changed, or nothing was ever
        scored). ``None`` if it no longer exists. Runs are serialized per Data Warehouse."""
        with Session(self._engine) as db, db.begin():
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord)
                .where(DataWarehouseRecord.id == data_warehouse_id)
                .with_for_update()
            ).first()
            if warehouse is None:
                return None
            report = evaluate(self._design(db, warehouse), self._registry, disabled=disabled)
            self._store(db, warehouse.id, report)
            return report

    # --- internals -------------------------------------------------------------------

    @staticmethod
    def _warehouse_id(db: Session, workspace_id: uuid.UUID) -> uuid.UUID | None:
        return db.scalars(
            sa.select(DataWarehouseRecord.id).where(
                DataWarehouseRecord.workspace_id == workspace_id
            )
        ).first()

    @staticmethod
    def _latest_run(db: Session, warehouse_id: uuid.UUID) -> ScoreRunRecord | None:
        return db.scalars(
            sa.select(ScoreRunRecord)
            .where(ScoreRunRecord.data_warehouse_id == warehouse_id)
            .order_by(ScoreRunRecord.created_at.desc(), ScoreRunRecord.id.desc())
            .limit(1)
        ).first()

    def _design(self, db: Session, warehouse: DataWarehouseRecord) -> Design:
        """The model as the checks see it, read in a handful of queries whatever its size."""
        tables = list(
            db.scalars(
                sa.select(DwTableRecord)
                .where(DwTableRecord.data_warehouse_id == warehouse.id)
                .order_by(sa.func.lower(DwTableRecord.name), DwTableRecord.id)
            )
        )
        ids = [t.id for t in tables]
        columns: dict[uuid.UUID, list[DwColumnRecord]] = defaultdict(list)
        for c in db.scalars(
            sa.select(DwColumnRecord)
            .where(DwColumnRecord.table_id.in_(ids))
            .order_by(DwColumnRecord.table_id, DwColumnRecord.ordinal)
        ):
            columns[c.table_id].append(c)
        column_mappings: dict[uuid.UUID, list[MappingFact]] = defaultdict(list)
        for m in db.scalars(
            sa.select(ColumnMappingRecord)
            .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
            .where(DwColumnRecord.table_id.in_(ids))
        ):
            column_mappings[m.dw_column_id].append(
                MappingFact(m.branch_id, m.mapping_type, m.sql_expression)
            )
        branches: dict[uuid.UUID, list[DesignBranch]] = defaultdict(list)
        for table_id, b in db.execute(
            sa.select(TableMappingRecord.dw_table_id, MappingBranchRecord)
            .join(
                MappingBranchRecord, MappingBranchRecord.table_mapping_id == TableMappingRecord.id
            )
            .where(TableMappingRecord.dw_table_id.in_(ids))
            .order_by(MappingBranchRecord.ordinal)
        ):
            branches[table_id].append(
                DesignBranch(
                    b.id, b.name, b.driving_input, b.joins, b.filters, b.group_by, b.having
                )
            )
        return Design(
            platform=warehouse.target_platform,
            naming_rules=NamingRules(**warehouse.naming_rules),
            tables=tuple(
                DesignTable(
                    id=t.id,
                    layer=t.layer,
                    name=t.name,
                    kind=t.kind,
                    fact_type=t.fact_type,
                    grain=t.grain,
                    is_aggregate=t.is_aggregate,
                    scd_type=t.scd_type,
                    is_conformed=t.is_conformed,
                    description=t.description,
                    columns=tuple(
                        DesignColumn(
                            id=c.id,
                            table_id=t.id,
                            name=c.name,
                            data_type=c.data_type,
                            role=c.role,
                            additivity=c.additivity,
                            references_table_id=c.references_table_id,
                            is_system=c.is_system,
                            needs_no_mapping=c.is_system or c.role in SYSTEM_ROLES,
                            mappings=tuple(column_mappings.get(c.id, ())),
                        )
                        for c in columns.get(t.id, ())
                    ),
                    branches=tuple(branches.get(t.id, ())),
                )
                for t in tables
            ),
        )

    def _store(self, db: Session, warehouse_id: uuid.UUID, report: ScoreReport) -> None:
        now = self._clock()
        previous = self._latest_run(db, warehouse_id)
        if previous is not None and now <= previous.created_at:
            now = previous.created_at + timedelta(microseconds=1)  # keep the runs in order
        db.add(
            ScoreRunRecord(
                id=uuid.uuid4(),
                data_warehouse_id=warehouse_id,
                score=report.score,
                grade=report.grade,
                per_layer={
                    item.layer: {
                        "score": item.score,
                        "grade": item.grade,
                        "tables": item.table_count,
                    }
                    for item in report.layers
                },
                created_at=now,
            )
        )
        db.execute(
            sa.delete(ScoreCheckResultRecord).where(
                ScoreCheckResultRecord.data_warehouse_id == warehouse_id
            )
        )
        db.add_all(
            ScoreCheckResultRecord(
                id=uuid.uuid4(),
                data_warehouse_id=warehouse_id,
                check_code=f.code,
                severity=f.severity,
                layer=f.layer,
                object_type=f.object_type,
                object_id=f.object_id,
                table_id=f.table_id,
                object_name=f.object_name,
                passed=f.passed,
                message=f.message[:MESSAGE_MAX_LENGTH],
            )
            for f in report.findings
        )
        old = (
            sa.select(ScoreRunRecord.id)
            .where(ScoreRunRecord.data_warehouse_id == warehouse_id)
            .order_by(ScoreRunRecord.created_at.desc(), ScoreRunRecord.id.desc())
            .offset(KEPT_RUNS)
        )
        db.flush()
        db.execute(sa.delete(ScoreRunRecord).where(ScoreRunRecord.id.in_(old)))

    def _view(self, run: ScoreRunRecord, rows: list[ScoreCheckResultRecord]) -> Score:
        by_table: dict[uuid.UUID, list[ScoreCheckResultRecord]] = defaultdict(list)
        for r in rows:
            by_table[r.table_id].append(r)
        tables = []
        for table_id, items in by_table.items():
            total = sum(SEVERITY_WEIGHTS[r.severity] for r in items)
            passed = sum(SEVERITY_WEIGHTS[r.severity] for r in items if r.passed)
            first = items[0]
            name = first.object_name.split(".", 1)[0]
            tables.append(TableView(table_id, first.layer, name, round(100 * passed / total, 2)))
        tables.sort(key=lambda t: (ALL_LAYERS.index(t.layer), t.score, t.name.lower()))
        failed = [r for r in rows if not r.passed]
        order = {"error": 0, "warning": 1, "info": 2}
        failed.sort(key=lambda r: (order[r.severity], r.object_name.lower(), r.check_code))
        errors = any(r.severity == "error" and r.layer != "staging" for r in failed)
        return Score(
            score=run.score,
            grade=run.grade,
            capped=run.score is not None and errors and grade_of(run.score) != run.grade,
            calculated_at=run.created_at,
            layers=_layer_views(run.per_layer),
            tables=tables,
            failed_checks=[self._failed(r) for r in failed],
            checks_passed=len(rows) - len(failed),
            checks_failed=len(failed),
        )

    def _failed(self, row: ScoreCheckResultRecord) -> FailedCheck:
        spec = self._registry.spec(row.check_code)
        return FailedCheck(
            check_code=row.check_code,
            title=spec.title if spec else row.check_code,
            category=spec.category if spec else "",
            severity=row.severity,
            layer=row.layer,
            object_type=row.object_type,
            object_id=row.object_id,
            table_id=row.table_id,
            object_name=row.object_name,
            message=row.message,
            fix_hint=spec.hint if spec else "",
            link=link_to(row.table_id, row.object_type, row.object_id),
        )
