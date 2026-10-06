"""``.../systems/{system_id}/tables/{table_id}``: Source enhancements (spec stories 61, 62).

Handlers only translate HTTP to ``EnhancementService`` calls; the service authorizes
every call through the workspaces module's policy.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .enhancement_service import EnhancementService
from .snapshot_api import Classification

router = APIRouter(tags=["sources"])


def enhancement_service(request: Request) -> EnhancementService:
    state = request.app.state
    clock = state.services.clock
    return EnhancementService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


EnhancementServiceDep = Annotated[EnhancementService, Depends(enhancement_service)]


class UpdateTableEnhancements(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    description: str | None = Field(
        default=None, description="Send null to clear it; leave it out to keep it."
    )
    tags: list[str] | None = Field(default=None, description="Replaces the tags.")
    is_sensitive: bool | None = None
    classification: Classification | None = Field(
        default=None, description="Send null to clear it; leave it out to keep it."
    )
    scd_hint: str | None = Field(
        default=None,
        description='E.g. "changes slowly, history matters". Send null to clear it.',
    )


class UpdateColumnEnhancements(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    description: str | None = Field(
        default=None, description="Send null to clear it; leave it out to keep it."
    )
    tags: list[str] | None = Field(default=None, description="Replaces the tags.")
    is_sensitive: bool | None = None


class TableEnhancements(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str | None
    tags: list[str]
    is_sensitive: bool
    classification: Classification | None
    scd_hint: str | None
    version: int


class ColumnEnhancements(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str | None
    tags: list[str]
    is_sensitive: bool
    version: int


# Null clears a text field; for the tags and the flag it means "leave as is".
_NULLABLE = ("description", "classification", "scd_hint")


def _changes(body: BaseModel) -> dict:
    return {
        k: v
        for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items()
        if v is not None or k in _NULLABLE
    }


@router.patch("/tables/{table_id}", operation_id="updateTableEnhancements")
def update_table_enhancements(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    table_id: uuid.UUID,
    body: UpdateTableEnhancements,
    user: CurrentUser,
    enhancements: EnhancementServiceDep,
) -> TableEnhancements:
    """Describe, tag, flag as sensitive and classify a table or view, with an SCD hint
    (owners and editors); fields left out stay as they are. 409 `version_conflict` if
    `version` is stale; 422 `invalid_enhancement`."""
    return TableEnhancements.model_validate(
        enhancements.update_table(
            user,
            workspace_id,
            system_id,
            table_id,
            version=body.version,
            changes=_changes(body),
        )
    )


@router.patch("/tables/{table_id}/columns/{column_id}", operation_id="updateColumnEnhancements")
def update_column_enhancements(
    workspace_id: uuid.UUID,
    system_id: uuid.UUID,
    table_id: uuid.UUID,
    column_id: uuid.UUID,
    body: UpdateColumnEnhancements,
    user: CurrentUser,
    enhancements: EnhancementServiceDep,
) -> ColumnEnhancements:
    """Describe, tag and flag a column as sensitive (owners and editors); fields left
    out stay as they are. 409 `version_conflict` if `version` is stale; 422
    `invalid_enhancement`."""
    return ColumnEnhancements.model_validate(
        enhancements.update_column(
            user,
            workspace_id,
            system_id,
            table_id,
            column_id,
            version=body.version,
            changes=_changes(body),
        )
    )
