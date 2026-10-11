"""``/api/v1/workspaces/{id}/data-warehouse/score/report``: the score report as Markdown or
XLSX (story 124).

Handlers only translate HTTP to ``ScoreExportService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from dawam.modules.auth import CurrentUser

from .score_api import score_service
from .score_export import MARKDOWN_MIME, XLSX_MIME, ScoreExportService

router = APIRouter(prefix="/workspaces/{workspace_id}/data-warehouse/score", tags=["score"])

ScoreReportFormatQuery = Annotated[
    Literal["md", "xlsx"],
    Query(description="`md` (Markdown) or `xlsx` (Summary, Layers, Tables, Failed checks)."),
]


def score_export_service(request: Request) -> ScoreExportService:
    return ScoreExportService(score_service(request))


ScoreExportServiceDep = Annotated[ScoreExportService, Depends(score_export_service)]


@router.get(
    "/report",
    operation_id="downloadScoreReport",
    response_class=Response,
    responses={
        200: {
            "content": {MARKDOWN_MIME: {}, XLSX_MIME: {}},
            "description": "The score report.",
        }
    },
)
def download_score_report(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    exports: ScoreExportServiceDep,
    format: ScoreReportFormatQuery = "md",
) -> Response:
    """The latest score as a report (any member): the overall, per-Layer and per-table
    scores, and every failed check with its severity, object and fix hint. To keep a copy,
    save it to the file area with `POST .../files/score-report`. 404 before the Data
    Warehouse is set up."""
    report = exports.export(user, workspace_id, fmt=format)
    return Response(
        report.data,
        media_type=report.mime,
        headers={"Content-Disposition": f'attachment; filename="{report.name}"'},
    )
