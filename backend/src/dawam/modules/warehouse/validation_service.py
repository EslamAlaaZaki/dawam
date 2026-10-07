"""Mapping validation, type warnings and coverage (spec §6.14, stories 104-106).

``run_validation`` reads every Core and Mart mapping of the Data Warehouse and returns the
problems an Editor must fix (``error``: SQL that does not parse, inputs outside the Layer
below, a non-aggregated output missing from the GROUP BY) or should look at (``warning``:
unmapped columns, a multi-branch table without an integration rule, and data-type
compatibility between a direct mapping's input and its target, see ``type_compat``).
Coverage is counted per table, per Layer and for the whole Data Warehouse, branch-aware as
``MappingService`` computes it; system columns and generated tables need no mapping and are
left out of the counts. Nothing is stored: both are computed on read.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError
from dawam.platform.hooks import ModelingProgress

from .mapping_service import MappingService, TableMappingView
from .tables import DataWarehouseRecord, DwColumnRecord, DwTableRecord
from .type_compat import check_types

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Problem:
    severity: Severity
    code: str
    message: str
    table_id: uuid.UUID
    table_name: str
    layer: str
    column_id: uuid.UUID | None = None
    column_name: str | None = None
    branch_id: uuid.UUID | None = None
    branch_name: str | None = None


@dataclass(frozen=True)
class Coverage:
    """Mappable columns (``total``) and how many are ``covered``; ``percent`` is 0-100."""

    total: int
    covered: int
    percent: int


@dataclass(frozen=True)
class TableCoverage:
    table_id: uuid.UUID
    table_name: str
    layer: str
    coverage: Coverage


@dataclass(frozen=True)
class LayerCoverage:
    layer: str
    tables: list[TableCoverage]
    coverage: Coverage


@dataclass(frozen=True)
class CoverageReport:
    layers: list[LayerCoverage]
    coverage: Coverage


@dataclass(frozen=True)
class ValidationReport:
    problems: list[Problem]
    error_count: int
    warning_count: int
    coverage: CoverageReport


def _coverage(total: int, covered: int) -> Coverage:
    return Coverage(total, covered, round(100 * covered / total) if total else 0)


class ValidationService:
    """Validation and coverage of a Workspace's mappings, for any member."""

    def __init__(
        self, engine: sa.Engine, *, workspaces: WorkspaceService, mappings: MappingService
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._mappings = mappings

    def run_validation(self, user: User, workspace_id: uuid.UUID) -> ValidationReport:
        """Problems of every Core and Mart mapping, errors first, with the coverage. 404
        ``not_set_up`` before the Data Warehouse exists."""
        views = self._views(user, workspace_id)
        problems = self._problems(views)
        problems.sort(key=lambda p: (p.severity != "error", p.layer, p.table_name))
        return ValidationReport(
            problems=problems,
            error_count=sum(p.severity == "error" for p in problems),
            warning_count=sum(p.severity == "warning" for p in problems),
            coverage=self._report(views),
        )

    def coverage(self, user: User, workspace_id: uuid.UUID) -> CoverageReport:
        """Mapping coverage per table, per Layer and for the Data Warehouse (any member)."""
        return self._report(self._views(user, workspace_id))

    def modeling_progress(self, workspace_id: uuid.UUID) -> list[ModelingProgress]:
        """DW Modeling progress per Layer, for the stage-progress port. Authorizes nothing:
        the stage-progress endpoint of ``workspaces`` has.

        Staging is generated, so it is complete once it has tables. A mapped Layer is
        ``complete`` when every mappable column is covered and nothing is in error, and
        ``in_progress`` once it has tables."""
        views = self._mappings.table_views(workspace_id)
        with Session(self._engine) as db:
            staging = db.scalar(
                sa.select(sa.func.count())
                .select_from(DwTableRecord)
                .join(
                    DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id
                )
                .where(
                    DataWarehouseRecord.workspace_id == workspace_id,
                    DwTableRecord.layer == "staging",
                )
            )
        result = [ModelingProgress("staging", "complete" if staging else "not_started")]
        errored = {p.layer for p in self._problems(views) if p.severity == "error"}
        for layer_report in self._report(views).layers:
            if not layer_report.tables:
                status = "not_started"
            elif layer_report.coverage.covered == layer_report.coverage.total and (
                layer_report.layer not in errored
            ):
                status = "complete"
            else:
                status = "in_progress"
            result.append(ModelingProgress(layer_report.layer, status))
        return result

    # -- internals ----------------------------------------------------------------------

    def _views(self, user: User, workspace_id: uuid.UUID) -> list[TableMappingView]:
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            exists = db.scalar(
                sa.select(DataWarehouseRecord.id).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            )
        if exists is None:
            raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
        return self._mappings.table_views(workspace_id)

    def _report(self, views: list[TableMappingView]) -> CoverageReport:
        layers: list[LayerCoverage] = []
        for layer in ("core", "mart"):
            tables = [
                TableCoverage(v.table_id, v.table_name, v.layer, _coverage(*self._count(v)))
                for v in views
                if v.layer == layer and v.kind != "generated"
            ]
            layers.append(
                LayerCoverage(
                    layer,
                    tables,
                    _coverage(
                        sum(t.coverage.total for t in tables),
                        sum(t.coverage.covered for t in tables),
                    ),
                )
            )
        return CoverageReport(
            layers,
            _coverage(
                sum(layer.coverage.total for layer in layers),
                sum(layer.coverage.covered for layer in layers),
            ),
        )

    @staticmethod
    def _count(view: TableMappingView) -> tuple[int, int]:
        mappable = [c for c in view.coverage if not c.system]
        return len(mappable), sum(c.covered for c in mappable)

    def _problems(self, views: list[TableMappingView]) -> list[Problem]:
        column_ids = {c.column_id for v in views for c in _mapped_columns(v)}
        column_ids |= {i.column_id for v in views for c in _mapped_columns(v) for i in c.inputs}
        with Session(self._engine) as db:
            columns = {
                c.id: c
                for c in db.scalars(
                    sa.select(DwColumnRecord).where(DwColumnRecord.id.in_(column_ids))
                )
            }
        problems: list[Problem] = []
        for view in views:
            if view.kind == "generated":
                continue
            problems.extend(self._table_problems(view, columns))
        return problems

    @staticmethod
    def _table_problems(
        view: TableMappingView, columns: dict[uuid.UUID, DwColumnRecord]
    ) -> list[Problem]:
        def problem(severity: Severity, code: str, message: str, **where) -> Problem:
            return Problem(
                severity, code, message, view.table_id, view.table_name, view.layer, **where
            )

        found: list[Problem] = []
        branch_names = {b.id: b.name for b in view.branches}
        for branch in view.branches:
            for error in branch.errors:
                found.append(
                    problem(
                        "error",
                        error["code"],
                        error["message"],
                        column_id=error.get("column_id"),
                        column_name=error.get("column_name"),
                        branch_id=branch.id,
                        branch_name=branch.name,
                    )
                )
        groups = (
            [(None, view.columns)] if not view.branches else [(b, b.columns) for b in view.branches]
        )
        for branch, mapped in groups:
            where = {"branch_id": branch.id, "branch_name": branch.name} if branch else {}
            for column in mapped:
                at = {"column_id": column.column_id, "column_name": column.column_name} | where
                if column.validation.get("unparsed"):
                    found.append(
                        problem(
                            "error",
                            "unparsed_sql",
                            _first_message(column) or "Unparsable SQL.",
                            **at,
                        )
                    )
                else:
                    found.extend(
                        problem("error", e.get("code", "invalid_mapping"), e["message"], **at)
                        for e in column.validation.get("errors", [])
                    )
                found.extend(_type_problems(problem, column, columns, at))
        for covered in view.coverage:
            if covered.covered or covered.system:
                continue
            names = [branch_names[b] for b in covered.missing_branch_ids if b in branch_names]
            detail = f" in {', '.join(names)}" if names else ""
            found.append(
                problem(
                    "warning",
                    "unmapped_column",
                    f"{covered.column_name} is not mapped{detail}.",
                    column_id=covered.column_id,
                    column_name=covered.column_name,
                )
            )
        if len(view.branches) > 1 and not (view.integration_rule or "").strip():
            found.append(
                problem(
                    "warning",
                    "missing_integration_rule",
                    f"{view.table_name} is fed by {len(view.branches)} branches but has no "
                    "integration rule (deduplication, survivorship).",
                )
            )
        return found


def _first_message(column) -> str:
    errors = column.validation.get("errors", [])
    return errors[0]["message"] if errors else ""


def _mapped_columns(view: TableMappingView):
    if view.branches:
        for branch in view.branches:
            yield from branch.columns
    else:
        yield from view.columns


def _type_problems(problem, column, columns: dict[uuid.UUID, DwColumnRecord], at) -> list[Problem]:
    """Type warnings for a direct mapping of exactly one column."""
    if column.mapping_type != "direct" or len(column.inputs) != 1:
        return []
    source, target = columns.get(column.inputs[0].column_id), columns.get(column.column_id)
    if source is None or target is None:
        return []
    return [
        problem("warning", w.code, f"{column.column_name}: {w.message}", **at)
        for w in check_types(
            source.data_type, target.data_type, source.is_nullable, target.is_nullable
        )
    ]
