"""``.../systems/{system_id}/import``: Schema Import (spec stories 47, 49, 50, 51).

Handlers only translate HTTP to ``SchemaImportService`` calls; the service authorizes
every call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .import_service import ImportResult as ImportResultRecord
from .import_service import SchemaImportService
from .internal.schema_import import ImportReport as ImportReportRecord
from .snapshot_api import SnapshotSummary

router = APIRouter(tags=["sources"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def import_service(request: Request) -> SchemaImportService:
    state = request.app.state
    clock = state.services.clock
    return SchemaImportService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


ImportServiceDep = Annotated[SchemaImportService, Depends(import_service)]


class ImportIssue(BaseModel):
    sheet: str | None = Field(description="The sheet it is about; empty for the whole file.")
    row: int | None = Field(description="The row in the sheet (the header is row 1).")
    message: str


class ImportReport(BaseModel):
    errors: list[ImportIssue] = Field(description="Any error means nothing can be saved.")
    warnings: list[ImportIssue] = Field(description="Saved only if the user accepts them.")
    schema_count: int
    table_count: int = Field(description="Tables and views.")
    column_count: int
    routine_count: int


class ImportOutcome(BaseModel):
    report: ImportReport
    imported: bool = Field(
        description="The import was accepted: a Snapshot was saved, or it matched the "
        "latest one (see `unchanged`). False means nothing was saved."
    )
    unchanged: bool = Field(
        description="No differences from the previous Snapshot, so no new one was created."
    )
    snapshot: SnapshotSummary | None


class ImportStatus(BaseModel):
    has_connection: bool
    latest_origin: Literal["connection", "import"] | None
    imported: bool = Field(
        description="The Source System works from a Schema Import, so `disabled_features` "
        "do not work until it has a live Connection."
    )
    disabled_features: list[str]


class ConnectionRequested(BaseModel):
    owners_notified: int


def _report(report: ImportReportRecord) -> ImportReport:
    return ImportReport(
        errors=[ImportIssue(sheet=i.sheet, row=i.row, message=i.message) for i in report.errors],
        warnings=[
            ImportIssue(sheet=i.sheet, row=i.row, message=i.message) for i in report.warnings
        ],
        schema_count=report.schema_count,
        table_count=report.table_count,
        column_count=report.column_count,
        routine_count=report.routine_count,
    )


def _outcome(result: ImportResultRecord) -> ImportOutcome:
    return ImportOutcome(
        report=_report(result.report),
        imported=result.imported,
        unchanged=result.unchanged,
        snapshot=SnapshotSummary.model_validate(result.snapshot) if result.snapshot else None,
    )


UploadedFiles = Annotated[
    list[UploadFile],
    File(
        description="The filled workbook (.xlsx), or one CSV per sheet named like it "
        "(`tables.csv`, `columns.csv`, ...)."
    ),
]


def _read(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    return [(f.filename or "", f.file.read()) for f in files]


@router.get(
    "/template",
    operation_id="downloadSchemaImportTemplate",
    response_class=Response,
    responses={200: {"content": {XLSX_MIME: {}}, "description": "The Excel template."}},
)
def download_template(
    workspace_id: uuid.UUID, system_id: uuid.UUID, user: CurrentUser, imports: ImportServiceDep
) -> Response:
    """The Schema Import Excel template (owners and editors): a sheet per kind of
    metadata with header rows, and a README that explains every column."""
    return Response(
        imports.template(user, workspace_id, system_id),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="schema-import-template.xlsx"'},
    )


@router.post("/validate", operation_id="validateSchemaImport")
def validate_import(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    files: UploadedFiles,
    user: CurrentUser,
    imports: ImportServiceDep,
) -> ImportReport:
    """Check an upload and report; nothing is saved (owners and editors), as multipart
    form data."""
    return _report(imports.validate(user, workspace_id, system_id, _read(files)))


@router.post("/upload", operation_id="uploadSchemaImport")
def upload_import(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    files: UploadedFiles,
    user: CurrentUser,
    imports: ImportServiceDep,
    accept_warnings: Annotated[
        bool, Form(description="Save even though the report has warnings.")
    ] = False,
    engine: Annotated[
        str | None,
        Form(
            description="`postgresql`, `sqlserver`, `mysql` or `oracle`: how the database "
            "treats name case, for matching names to existing Source Objects. A Connection's "
            "engine wins; without either, names match exactly."
        ),
    ] = None,
) -> ImportOutcome:
    """Validate an upload and, when the report has no errors (and no warnings, or they
    are accepted), save it as a Snapshot with `origin = import` (owners and editors), as
    multipart form data. With errors, or unaccepted warnings, nothing is saved:
    `imported` is false and the report says why. 422 `unsupported_engine`."""
    return _outcome(
        imports.upload(
            user,
            workspace_id,
            system_id,
            _read(files),
            accept_warnings=accept_warnings,
            engine=engine,
        )
    )


@router.get("", operation_id="getSchemaImportStatus")
def get_import_status(
    workspace_id: uuid.UUID, system_id: uuid.UUID, user: CurrentUser, imports: ImportServiceDep
) -> ImportStatus:
    """Whether the Source System works from a Schema Import and which features that
    disables (any member)."""
    status = imports.status(user, workspace_id, system_id)
    return ImportStatus(
        has_connection=status.has_connection,
        latest_origin=status.latest_origin,
        imported=status.imported,
        disabled_features=status.disabled_features,
    )


@router.post("/connection-requests", operation_id="requestConnection", status_code=202)
def request_connection(
    workspace_id: uuid.UUID, system_id: uuid.UUID, user: CurrentUser, imports: ImportServiceDep
) -> ConnectionRequested:
    """Ask the Workspace's owners to supply a live Connection (owners and editors); they
    are notified. 409 `connection_exists` if the Source System already has one."""
    return ConnectionRequested(
        owners_notified=imports.request_connection(user, workspace_id, system_id)
    )
