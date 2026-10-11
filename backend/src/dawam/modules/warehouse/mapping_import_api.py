"""``POST /api/v1/workspaces/{id}/data-warehouse/mapping-sheet/import``: a filled-in mapping
sheet comes back as a validation report and a Change Set (story 109).

Handlers only translate HTTP to ``MappingImportService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.changesets import ChangeSetService, ObjectHandlers
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService

from .mapping_import import MAX_BYTES, MappingImportReport, MappingImportService
from .mapping_service import MappingService
from .service import DataWarehouseService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse", tags=["data-warehouse"])


def mapping_import_service(request: Request) -> MappingImportService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    notifications = NotificationService(state.engine, clock=clock)
    return MappingImportService(
        state.engine,
        workspaces=workspaces,
        warehouses=DataWarehouseService(state.engine, workspaces=workspaces, clock=clock),
        mappings=MappingService(state.engine, workspaces=workspaces, clock=clock),
        change_sets=ChangeSetService(
            state.engine,
            workspaces=workspaces,
            handlers=getattr(state, "change_set_handlers", None) or ObjectHandlers(),
            notifications=notifications,
            clock=clock,
            conversations=getattr(state, "readable_conversations", None),
        ),
    )


MappingImportServiceDep = Annotated[MappingImportService, Depends(mapping_import_service)]


class MappingImportIssue(BaseModel):
    sheet: str = Field(description="`Mapping` or `Branches`.")
    row: int | None = Field(description="The row in the sheet (the header is row 1).")
    message: str


class MappingImportReportOut(BaseModel):
    errors: list[MappingImportIssue] = Field(
        description="Rows that failed validation. They propose nothing; the rest still do."
    )
    rows: int = Field(description="Mapping and branch rows read.")
    ignored_staging_rows: int
    unchanged: int = Field(description="Rows that already match the Data Warehouse.")
    changes: int = Field(description="Items proposed, or that would be on a dry run.")


class MappingImportOutcome(BaseModel):
    report: MappingImportReportOut
    change_set_id: uuid.UUID | None = Field(
        description="The pending Change Set (origin `import`) to review at `/change-sets/{id}`; "
        "null on a dry run and when nothing differs."
    )


def _report(report: MappingImportReport) -> MappingImportReportOut:
    return MappingImportReportOut(
        errors=[
            MappingImportIssue(sheet=e.sheet, row=e.row, message=e.message) for e in report.errors
        ],
        rows=report.rows,
        ignored_staging_rows=report.ignored_staging_rows,
        unchanged=report.unchanged,
        changes=report.changes,
    )


@router.post("/mapping-sheet/import", operation_id="importMappingSheet")
def import_mapping_sheet(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    imports: MappingImportServiceDep,
    file: Annotated[UploadFile, File(description="The mapping sheet (.xlsx).")],
    dry_run: Annotated[
        bool,
        Query(description="Only validate and report; propose nothing. The default."),
    ] = True,
) -> MappingImportOutcome:
    """Import a mapping sheet in the layout of the export (owners and editors). It reports
    first: rows naming a table, column or branch that does not exist fail validation and
    create nothing, Staging rows are ignored, and a `lookup`, `system` or `hashed` mapping
    may only be repeated as it is. With `dry_run=false`, everything that differs from the Data
    Warehouse becomes one pending Change Set (origin `import`) for review: mappings are
    created or their type, rule and SQL changed, and branch join paths, filters, group-by and
    having changed. Approval states are never changed by the import. An unreadable file is 422
    `invalid_sheet`; 404 before the Data Warehouse is set up."""
    data = file.file.read(MAX_BYTES + 1)
    result = imports.import_sheet(
        user, workspace_id, data, filename=file.filename or "", dry_run=dry_run
    )
    return MappingImportOutcome(
        report=_report(result.report),
        change_set_id=result.change_set.change_set.id if result.change_set else None,
    )
