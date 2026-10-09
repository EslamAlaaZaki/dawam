"""``/api/v1/workspaces/{id}/data-warehouse/staging``: generate the Staging Layer (spec §6.7).

Handlers only translate HTTP to ``StagingService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .staging_service import StagingService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/staging", tags=["data-warehouse-staging"]
)


def staging_service(request: Request) -> StagingService:
    state = request.app.state
    clock = state.services.clock
    return StagingService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


StagingServiceDep = Annotated[StagingService, Depends(staging_service)]


class StagingFlag(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    table_name: str
    column_name: str | None = Field(description="Null for a flag on the table itself.")
    code: str = Field(
        description="`placeholder`, `truncated`, `collision`, `lossy_type` or `fallback_type`."
    )
    message: str


class StagingResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tables_created: int
    columns_created: int = Field(description="Including the audit columns.")
    tables_existing: int = Field(
        description="Source tables that already had a Staging Table; left as they are."
    )
    flags: list[StagingFlag] = Field(description="What to review, for the tables just created.")


@router.post("/generate", operation_id="generateStaging")
def generate_staging(
    workspace_id: uuid.UUID, user: CurrentUser, staging: StagingServiceDep
) -> StagingResult:
    """Generate the Staging Layer from the Source Schema (owners and editors; software,
    no AI): one Staging Table per source base table (and per view opted in), named
    `stg_<system code>_<database schema>_<table>`, with translated column types, the audit
    columns, and `direct` mappings and lineage from the source. Names and types that needed
    a placeholder, a hash suffix or a lossy translation are flagged for review. Running it
    again adds only what is new. 404 `not_set_up` before the Data Warehouse is set up."""
    return StagingResult.model_validate(staging.generate(user, workspace_id))
