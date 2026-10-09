"""``/api/v1/workspaces/{id}/data-warehouse/tables``: the Core and Mart model editor
(spec stories 89-93a).

Handlers only translate HTTP to ``ModelService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import model_service as svc
from .model_service import ModelService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/tables", tags=["data-warehouse-model"]
)


def model_service(request: Request) -> ModelService:
    state = request.app.state
    clock = state.services.clock
    return ModelService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


ModelServiceDep = Annotated[ModelService, Depends(model_service)]

Layer = Literal["staging", "core", "mart"]
TableKind = Literal["fact", "dimension", "bridge"]
FactType = Literal["transactional", "periodic_snapshot", "accumulating_snapshot", "factless"]
ColumnRole = Literal[
    "sk",
    "nk",
    "fk",
    "measure",
    "attribute",
    "degenerate_dimension",
    "audit",
    "scd_valid_from",
    "scd_valid_to",
    "scd_current_flag",
    "row_hash",
]
Additivity = Literal["additive", "semi_additive", "non_additive"]
DefaultValue = str | int | float | bool | None


class DwDataType(BaseModel):
    """A neutral data type, translated to the target platform only when DDL is produced."""

    model_config = ConfigDict(from_attributes=True)

    type: str = Field(description="E.g. integer, bigint, decimal, string, date, timestamp.")
    length: int | None = Field(None, description="For char, string and binary.")
    precision: int | None = Field(None, description="For decimal.")
    scale: int | None = Field(None, description="For decimal.")


class DwUnknownMember(BaseModel):
    surrogate_key: int = Field(description="Always -1.")
    defaults: dict[str, DefaultValue] = Field(
        description="Column name to its value in the unknown-member row."
    )


class NamingViolation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: Literal["case_style", "prefix"]
    message: str
    expected: str = Field(description="The prefix, or the case style, the name should follow.")


class ReviewFlag(BaseModel):
    code: str = Field(
        description="`placeholder`, `truncated`, `collision`, `lossy_type` or `fallback_type`."
    )
    message: str


class DwColumn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    ordinal: int
    data_type: DwDataType
    is_nullable: bool
    role: ColumnRole
    additivity: Additivity | None
    scd_type_override: int | None
    references_table_id: uuid.UUID | None
    role_name: str | None
    description: str
    semantic_type: str | None
    is_system: bool = Field(description="DAWAM maintains it (SCD2 housekeeping).")
    version: int
    naming_violations: list[NamingViolation] = Field(
        description="The Data Warehouse's naming rules this name breaks (a warning, not an error)."
    )
    review_flags: list[ReviewFlag] = Field(
        description="What staging generation flagged on a staging column (empty otherwise)."
    )
    status: Literal["present", "source_removed"] = Field(
        default="present",
        description="`source_removed`: the source column is gone; the column stays, flagged.",
    )


class DwTableSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    layer: Layer
    name: str
    kind: str
    fact_type: FactType | None
    grain: str | None
    is_aggregate: bool
    scd_type: int | None
    is_conformed: bool
    description: str
    column_count: int
    version: int
    naming_violation_count: int = Field(
        description="How many naming rules the table's own name breaks."
    )
    status: Literal["present", "source_removed"] = Field(
        default="present",
        description="`source_removed`: the source table is gone; the Staging Table stays, flagged.",
    )


class DwTable(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    layer: Layer
    name: str
    kind: str
    fact_type: FactType | None
    grain: str | None
    is_aggregate: bool
    scd_type: int | None = Field(description="A dimension's SCD type (0, 1 or 2).")
    is_conformed: bool
    unknown_member: DwUnknownMember | None = Field(description="Dimensions only.")
    description: str
    columns: list[DwColumn]
    created_at: datetime
    updated_at: datetime
    version: int
    naming_violations: list[NamingViolation]
    review_flags: list[ReviewFlag] = Field(
        description="What staging generation flagged on a Staging Table (empty otherwise)."
    )
    status: Literal["present", "source_removed"] = Field(
        default="present",
        description="`source_removed`: the source table is gone; the Staging Table stays, flagged.",
    )


class DwTableList(BaseModel):
    items: list[DwTableSummary]


class CreateDwTableRequest(BaseModel):
    layer: Layer = Field(description="`core` or `mart`.")
    name: str
    kind: TableKind
    grain: str | None = Field(None, description="Required for a fact: what one row stands for.")
    fact_type: FactType | None = Field(None, description="Required for a fact.")
    is_aggregate: bool | None = None
    scd_type: int | None = Field(None, description="A dimension's SCD type; default 1.")
    is_conformed: bool | None = None
    description: str | None = None


class CreateGeneratedDwTableRequest(BaseModel):
    kind: Literal["date", "time"] = Field(description="The built-in dimension to add.")
    layer: Layer = Field("core", description="`core` (default) or `mart`.")


class UpdateDwTableRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    name: str | None = None
    description: str | None = None
    grain: str | None = None
    fact_type: FactType | None = None
    is_aggregate: bool | None = None
    scd_type: int | None = Field(
        None, description="Making it 2 adds the SCD2 housekeeping columns."
    )
    is_conformed: bool | None = None
    unknown_member_defaults: dict[str, DefaultValue] | None = Field(
        None, description="Replaces the unknown member's default values (dimensions)."
    )


class CreateDwColumnRequest(BaseModel):
    name: str
    data_type: DwDataType
    role: ColumnRole
    is_nullable: bool | None = None
    additivity: Additivity | None = Field(None, description="For a measure.")
    scd_type_override: int | None = Field(None, description="For a dimension attribute.")
    references_table_id: uuid.UUID | None = Field(None, description="Required for a foreign key.")
    role_name: str | None = Field(None, description="For a role-playing foreign key.")
    description: str | None = None
    semantic_type: str | None = None


class UpdateDwColumnRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    name: str | None = None
    data_type: DwDataType | None = None
    role: ColumnRole | None = None
    is_nullable: bool | None = None
    additivity: Additivity | None = None
    scd_type_override: int | None = None
    references_table_id: uuid.UUID | None = None
    role_name: str | None = None
    description: str | None = None
    semantic_type: str | None = None


# Null clears these; for every other field, null is the same as leaving it out.
_NULLABLE_COLUMN = (
    "additivity",
    "scd_type_override",
    "references_table_id",
    "role_name",
    "semantic_type",
)


def _fields(
    body: BaseModel, *, nullable: tuple[str, ...] = (), skip: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        k: v
        for k, v in body.model_dump(exclude_unset=True, exclude={"version", *skip}).items()
        if v is not None or k in nullable
    }


def _column(column: svc.ModelColumn) -> DwColumn:
    return DwColumn.model_validate(column)


def _table(table: svc.ModelTable) -> DwTable:
    return DwTable(
        **{
            **{f: getattr(table, f) for f in DwTable.model_fields if f != "columns"},
            "columns": [_column(c) for c in table.columns],
        }
    )


@router.get("", operation_id="listDwTables")
def list_tables(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    model: ModelServiceDep,
    layer: Layer | None = None,
) -> DwTableList:
    """The DW Schema's tables, optionally of one Layer (any member). Empty before the
    Data Warehouse is set up."""
    return DwTableList(
        items=[
            DwTableSummary.model_validate(t)
            for t in model.list_tables(user, workspace_id, layer=layer)
        ]
    )


@router.post("", operation_id="createDwTable", status_code=201)
def create_table(
    workspace_id: uuid.UUID, body: CreateDwTableRequest, user: CurrentUser, model: ModelServiceDep
) -> DwTable:
    """Create a Core or Mart fact, dimension or bridge (editors and owners). A fact needs a
    grain and a fact type. A dimension gets a surrogate key and an unknown member (key -1);
    an SCD2 dimension gets the housekeeping columns; a bridge gets its group key. 422
    `invalid_model`; 409 `name_taken`; 404 `not_set_up`."""
    return _table(model.create_table(user, workspace_id, fields=_fields(body)))


@router.post("/generated", operation_id="createGeneratedDwTable", status_code=201)
def create_generated_table(
    workspace_id: uuid.UUID,
    body: CreateGeneratedDwTableRequest,
    user: CurrentUser,
    model: ModelServiceDep,
) -> DwTable:
    """Add the built-in date or time dimension (editors and owners): a `generated`, conformed
    table that needs no mapping. The date dimension's columns follow the Data Warehouse's
    date-dimension settings (Gregorian attributes, optional Hijri and fiscal ones; the
    weekend flag and range apply to the seed rows). Its seed file is delivered with the DDL
    package. 409 `name_taken` when it already exists; 404 `not_set_up`."""
    return _table(
        model.create_generated_table(user, workspace_id, which=body.kind, layer=body.layer)
    )


@router.get("/{dw_table_id}", operation_id="getDwTable")
def get_table(
    workspace_id: uuid.UUID, dw_table_id: uuid.UUID, user: CurrentUser, model: ModelServiceDep
) -> DwTable:
    """A table with its columns (any member)."""
    return _table(model.get_table(user, workspace_id, dw_table_id))


@router.patch("/{dw_table_id}", operation_id="updateDwTable")
def update_table(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    body: UpdateDwTableRequest,
    user: CurrentUser,
    model: ModelServiceDep,
) -> DwTable:
    """Change a table (editors and owners); fields left out stay as they are. 409
    `version_conflict` if `version` is stale; 422 `invalid_model`."""
    return _table(
        model.update_table(
            user, workspace_id, dw_table_id, version=body.version, changes=_fields(body)
        )
    )


@router.delete(
    "/{dw_table_id}", operation_id="deleteDwTable", status_code=204, response_class=Response
)
def delete_table(
    workspace_id: uuid.UUID, dw_table_id: uuid.UUID, user: CurrentUser, model: ModelServiceDep
) -> Response:
    """Delete a table and its columns (editors and owners). 409 `table_referenced` while
    another table's foreign key points at it."""
    model.delete_table(user, workspace_id, dw_table_id)
    return Response(status_code=204)


@router.post("/{dw_table_id}/columns", operation_id="createDwColumn", status_code=201)
def create_column(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    body: CreateDwColumnRequest,
    user: CurrentUser,
    model: ModelServiceDep,
) -> DwColumn:
    """Add a column (editors and owners). A measure sits on a fact and may declare its
    additivity; a foreign key references a dimension, with an optional role name. 422
    `invalid_model`; 409 `name_taken`."""
    return _column(model.add_column(user, workspace_id, dw_table_id, fields=_fields(body)))


@router.patch("/{dw_table_id}/columns/{dw_column_id}", operation_id="updateDwColumn")
def update_column(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    dw_column_id: uuid.UUID,
    body: UpdateDwColumnRequest,
    user: CurrentUser,
    model: ModelServiceDep,
) -> DwColumn:
    """Change a column (editors and owners); fields left out stay as they are, null
    clears an optional one. 409 `version_conflict` if `version` is stale; 422
    `system_column` for an SCD2 housekeeping column."""
    return _column(
        model.update_column(
            user,
            workspace_id,
            dw_table_id,
            dw_column_id,
            version=body.version,
            changes=_fields(body, nullable=_NULLABLE_COLUMN),
        )
    )


@router.delete(
    "/{dw_table_id}/columns/{dw_column_id}",
    operation_id="deleteDwColumn",
    status_code=204,
    response_class=Response,
)
def delete_column(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    dw_column_id: uuid.UUID,
    user: CurrentUser,
    model: ModelServiceDep,
) -> Response:
    """Delete a column (editors and owners); not an SCD2 housekeeping one."""
    model.delete_column(user, workspace_id, dw_table_id, dw_column_id)
    return Response(status_code=204)
