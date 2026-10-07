"""``/api/v1/workspaces/{id}/data-warehouse/validation`` and ``.../coverage``.

Handlers only translate HTTP to ``ValidationService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import validation_service as svc
from .mapping_service import MappingService
from .validation_service import ValidationService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse", tags=["data-warehouse"])


def validation_service(request: Request) -> ValidationService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return ValidationService(
        state.engine,
        workspaces=workspaces,
        mappings=MappingService(state.engine, workspaces=workspaces, clock=clock),
    )


ValidationServiceDep = Annotated[ValidationService, Depends(validation_service)]


class CoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int = Field(description="Columns that need a mapping (system columns excluded).")
    covered: int = Field(description="Of those, mapped in every branch (or not available there).")
    percent: int = Field(description="`covered` of `total`, 0-100; 0 when `total` is 0.")


class TableCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    table_name: str
    layer: Literal["core", "mart"]
    coverage: CoverageOut


class LayerCoverageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layer: Literal["core", "mart"]
    tables: list[TableCoverageOut]
    coverage: CoverageOut


class CoverageReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    layers: list[LayerCoverageOut] = Field(description="Core, then Mart.")
    coverage: CoverageOut = Field(description="The whole Data Warehouse.")


class ProblemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    severity: Literal["error", "warning"]
    code: str = Field(
        description="`unparsed_sql`, `not_in_group_by` or another mapping error code (errors); "
        "`unmapped_column`, `missing_integration_rule`, `may_truncate`, `may_lose_precision`, "
        "`may_fail_conversion` or `nullable_into_required` (warnings)."
    )
    message: str
    table_id: uuid.UUID
    table_name: str
    layer: Literal["core", "mart"]
    column_id: uuid.UUID | None
    column_name: str | None
    branch_id: uuid.UUID | None
    branch_name: str | None


class ValidationReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    problems: list[ProblemOut] = Field(description="Errors first, then by Layer and table.")
    error_count: int
    warning_count: int
    coverage: CoverageReport


def _report(report: svc.ValidationReport) -> ValidationReport:
    return ValidationReport.model_validate(report)


@router.get("/validation", operation_id="runValidation")
def run_validation(
    workspace_id: uuid.UUID, user: CurrentUser, validation: ValidationServiceDep
) -> ValidationReport:
    """Validate every Core and Mart mapping (any member): errors (SQL that does not parse,
    a missing GROUP BY column) and warnings (unmapped columns, a multi-branch table without
    an integration rule, data-type compatibility such as truncation between a direct
    mapping's input and its target), with the mapping coverage. Computed on read. 404
    `not_set_up` before the Data Warehouse is set up."""
    return _report(validation.run_validation(user, workspace_id))


@router.get("/coverage", operation_id="getMappingCoverage")
def get_coverage(
    workspace_id: uuid.UUID, user: CurrentUser, validation: ValidationServiceDep
) -> CoverageReport:
    """Mapping coverage per table, per Layer and for the whole Data Warehouse (any member),
    branch-aware: a column is covered when every branch maps it or marks it not available."""
    return CoverageReport.model_validate(validation.coverage(user, workspace_id))
