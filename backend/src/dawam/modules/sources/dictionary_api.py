"""``.../systems/{system_id}/data-dictionary``: the data dictionary as XLSX (story 129).

Handlers only translate HTTP to ``DataDictionaryService`` calls.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .dictionary_service import XLSX_MIME, DataDictionaryService
from .service import SourceSystemService
from .snapshot_service import SnapshotService

router = APIRouter(tags=["sources"])


def data_dictionary_service(request: Request) -> DataDictionaryService:
    state = request.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    return DataDictionaryService(
        SnapshotService(
            state.engine,
            workspaces=workspaces,
            jobs=state.services.jobs,
            encryption_key=state.settings.encryption_key.get_secret_value(),
            clock=clock,
        ),
        SourceSystemService(state.engine, workspaces=workspaces, clock=clock),
    )


DataDictionaryServiceDep = Annotated[DataDictionaryService, Depends(data_dictionary_service)]


@router.get(
    "/data-dictionary",
    operation_id="downloadDataDictionary",
    response_class=Response,
    responses={200: {"content": {XLSX_MIME: {}}, "description": "The data dictionary."}},
)
def download_data_dictionary(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    user: CurrentUser,
    dictionaries: DataDictionaryServiceDep,
) -> Response:
    """The Source System's data dictionary as an Excel workbook (any member): a `Tables`
    and a `Columns` sheet with types, descriptions, tags, classifications and PII
    categories, never values. 404 before the first Snapshot. To keep a copy, save it to the
    file area with `POST .../files/data-dictionary`."""
    export = dictionaries.export(user, workspace_id, system_id)
    return Response(
        export.data,
        media_type=export.mime,
        headers={"Content-Disposition": f'attachment; filename="{export.name}"'},
    )
