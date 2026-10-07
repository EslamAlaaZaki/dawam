"""Column mappings and lineage (spec §6.14, stories 99, 100, 104):

- ``/workspaces/{id}/data-warehouse/tables/{table_id}/mapping``: a Core or Mart table's
  mapping from the Layer below, one column at a time.
- ``/workspaces/{id}/data-warehouse/lineage``: graph queries over the stored edges.

Handlers only translate HTTP to ``MappingService`` calls; the service authorizes every
call through the workspaces policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import mapping_service as svc
from .mapping_service import MappingService

router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/mapping",
    tags=["data-warehouse-mappings"],
)
lineage_router = APIRouter(
    prefix="/workspaces/{workspace_id}/data-warehouse/lineage", tags=["data-warehouse-mappings"]
)


def mapping_service(request: Request) -> MappingService:
    state = request.app.state
    clock = state.services.clock
    return MappingService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


MappingServiceDep = Annotated[MappingService, Depends(mapping_service)]

MappingType = Literal["direct", "derived", "constant", "unmapped", "not_in_branch"]


class MappingInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: Literal["value", "uses"]
    table_id: uuid.UUID
    table_name: str
    column_id: uuid.UUID
    column_name: str


class MappingValidation(BaseModel):
    unparsed: bool = Field(description="The SQL could not be parsed; it produces no edges.")
    errors: list[dict[str, str]] = Field(description="Each error's `code` and `message`.")


class ColumnMapping(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = Field(description="Null until the column's mapping is first saved.")
    column_id: uuid.UUID
    column_name: str
    mapping_type: MappingType
    rule_text: str = Field(description="The transformation rule, in plain language.")
    sql_expression: str = Field(
        description="SQL in the target dialect, reading `table.column` of the Layer below."
    )
    inputs: list[MappingInput] = Field(
        description="Columns the SQL reads into the result; derived from the SQL, never set."
    )
    uses: list[MappingInput] = Field(
        description="Columns that only steer the result (conditions); derived from the SQL."
    )
    validation: MappingValidation
    version: int = Field(description="0 until first saved.")


class BranchError(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: Literal["not_in_group_by"]
    column_id: uuid.UUID
    column_name: str
    message: str


class MappingBranch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int = Field(description="1-based; branches are combined in this order.")
    name: str
    driving_input: str = Field(description="The branch's driving table, in the target dialect.")
    joins: str = Field(description="JOIN clauses, in the target dialect.")
    filters: str = Field(description="The WHERE condition, in the target dialect.")
    group_by: str | None
    having: str | None
    version: int
    columns: list[ColumnMapping] = Field(
        description="The branch's mapping of every non-system column; `unmapped` until saved."
    )
    errors: list[BranchError] = Field(
        description="For an aggregate table, each plain output the GROUP BY does not cover."
    )


class ColumnCoverage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    column_id: uuid.UUID
    column_name: str
    system: bool = Field(description="DAWAM-generated: mapped at table level, always covered.")
    covered: bool = Field(description="Mapped or `not_in_branch` in every branch.")
    missing_branch_ids: list[uuid.UUID]


class TableMapping(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    table_id: uuid.UUID
    table_name: str
    layer: Literal["core", "mart"]
    source_layer: Literal["staging", "core"]
    integration_rule: str | None
    match_keys: list[str]
    notes: str
    version: int = Field(description="0 until first saved.")
    columns: list[ColumnMapping] = Field(
        description="Every column of the table with its table-level mapping, by ordinal."
    )
    is_aggregate: bool
    branches: list[MappingBranch]
    coverage: list[ColumnCoverage] = Field(description="Branch coverage, per column.")
    sql: str | None = Field(
        description="The branches' queries combined with UNION ALL; null without branches."
    )


class BranchRequest(BaseModel):
    name: str
    driving_input: str
    joins: str = ""
    filters: str = ""
    group_by: str | None = None
    having: str | None = None


class UpdateBranchRequest(BaseModel):
    version: int
    name: str | None = None
    driving_input: str | None = None
    joins: str | None = None
    filters: str | None = None
    group_by: str | None = None
    having: str | None = None


class SaveColumnMappingRequest(BaseModel):
    mapping_type: MappingType = Field(description="`not_in_branch` (NULL) only inside a branch.")
    rule_text: str = ""
    sql_expression: str = ""
    version: int = Field(0, description="The mapping's version; 0 to create it.")


class UpdateTableMappingRequest(BaseModel):
    version: int = Field(0, description="The table mapping's version; 0 before its first save.")
    integration_rule: str | None = None
    match_keys: list[str] | None = None
    notes: str | None = None


class LineageEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: Literal["value", "uses", "lookup", "kpi"]
    from_type: str
    from_id: uuid.UUID
    from_label: str = Field(description="`table.column` for a column, else the name.")
    to_type: str
    to_id: uuid.UUID
    to_label: str


class LineageGraph(BaseModel):
    edges: list[LineageEdge]


def _table(view: svc.TableMappingView) -> TableMapping:
    return TableMapping.model_validate(view)


@router.get("", operation_id="getTableMapping")
def get_mapping(
    workspace_id: uuid.UUID, dw_table_id: uuid.UUID, user: CurrentUser, mappings: MappingServiceDep
) -> TableMapping:
    """A Core or Mart table's mapping from the Layer below, one entry per column (any
    member); a column is `unmapped` until saved. 422 `invalid_mapping` for a Staging Table."""
    return _table(mappings.get_mapping(user, workspace_id, dw_table_id))


@router.patch("", operation_id="updateTableMapping")
def update_mapping(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    body: UpdateTableMappingRequest,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> TableMapping:
    """Change the table's integration rule, match keys or notes (editors and owners);
    fields left out stay as they are. 409 `version_conflict`."""
    changes: dict[str, Any] = body.model_dump(exclude_unset=True, exclude={"version"})
    return _table(
        mappings.update_table_mapping(
            user, workspace_id, dw_table_id, version=body.version, changes=changes
        )
    )


@router.put("/columns/{dw_column_id}", operation_id="saveColumnMapping")
def save_column_mapping(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    dw_column_id: uuid.UUID,
    body: SaveColumnMappingRequest,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> ColumnMapping:
    """Save a column's mapping (editors and owners). The SQL is the master: its inputs
    must be `table.column` of the Layer directly below, and lineage edges are derived from
    it, replacing the previous ones. SQL that does not parse is saved with
    `validation.unparsed` and an error, and produces no edges. 422 `invalid_mapping`;
    409 `version_conflict`."""
    return ColumnMapping.model_validate(
        mappings.save_column_mapping(
            user,
            workspace_id,
            dw_table_id,
            dw_column_id,
            version=body.version,
            fields=body.model_dump(exclude={"version"}),
        )
    )


@router.post("/branches", operation_id="createMappingBranch", status_code=201)
def create_branch(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    body: BranchRequest,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> TableMapping:
    """Add a branch (editors and owners): one row-set of the table, combined with the
    others by UNION ALL. Its SQL parts must name tables of the Layer directly below.
    422 `invalid_mapping`."""
    return _table(mappings.create_branch(user, workspace_id, dw_table_id, fields=body.model_dump()))


@router.patch("/branches/{branch_id}", operation_id="updateMappingBranch")
def update_branch(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    branch_id: uuid.UUID,
    body: UpdateBranchRequest,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> TableMapping:
    """Change a branch (editors and owners); fields left out stay. 409 `version_conflict`."""
    return _table(
        mappings.update_branch(
            user,
            workspace_id,
            dw_table_id,
            branch_id,
            version=body.version,
            changes=body.model_dump(exclude_unset=True, exclude={"version"}),
        )
    )


@router.delete("/branches/{branch_id}", operation_id="deleteMappingBranch", status_code=204)
def delete_branch(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> None:
    """Remove a branch with its column mappings and lineage edges (editors and owners)."""
    mappings.delete_branch(user, workspace_id, dw_table_id, branch_id)


@router.put("/branches/{branch_id}/columns/{dw_column_id}", operation_id="saveBranchColumnMapping")
def save_branch_column_mapping(
    workspace_id: uuid.UUID,
    dw_table_id: uuid.UUID,
    branch_id: uuid.UUID,
    dw_column_id: uuid.UUID,
    body: SaveColumnMappingRequest,
    user: CurrentUser,
    mappings: MappingServiceDep,
) -> ColumnMapping:
    """Save a column's mapping within a branch (editors and owners), as for the table
    level. `not_in_branch` marks the column NULL in this branch. System columns are
    table-level only. 422 `invalid_mapping`; 409 `version_conflict`."""
    return ColumnMapping.model_validate(
        mappings.save_column_mapping(
            user,
            workspace_id,
            dw_table_id,
            dw_column_id,
            version=body.version,
            fields=body.model_dump(exclude={"version"}),
            branch_id=branch_id,
        )
    )


@lineage_router.get("/columns/{dw_column_id}", operation_id="getColumnLineage")
def get_lineage(
    workspace_id: uuid.UUID,
    dw_column_id: uuid.UUID,
    user: CurrentUser,
    mappings: MappingServiceDep,
    direction: Literal["upstream", "downstream"] = "upstream",
) -> LineageGraph:
    """Every lineage edge on a path to (`upstream`) or from (`downstream`) a DW column
    (any member). `uses` edges point at the table they steer."""
    return LineageGraph(
        edges=[
            LineageEdge.model_validate(e)
            for e in mappings.lineage(user, workspace_id, dw_column_id, direction)
        ]
    )
