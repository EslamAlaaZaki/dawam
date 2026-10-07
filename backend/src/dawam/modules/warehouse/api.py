"""``/api/v1/workspaces/{id}/data-warehouse``: the "Set up Data Warehouse" step, and
``/api/v1/data-warehouse/platforms``: what each target platform allows.

Handlers only translate HTTP to ``DataWarehouseService`` calls; the service authorizes
every call through the Workspace policy, so no handler looks at roles.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from . import service as svc
from .mapping_api import lineage_router
from .mapping_api import router as mapping_router
from .model_api import router as model_router
from .platforms import PLATFORM_PROFILES, TARGET_PLATFORMS, TargetPlatform
from .service import CaseStyle, DataWarehouseService, Weekday

router = APIRouter(tags=["data-warehouse"])
router.include_router(model_router)
router.include_router(mapping_router)
router.include_router(lineage_router)


def data_warehouse_service(request: Request) -> DataWarehouseService:
    state = request.app.state
    clock = state.services.clock
    return DataWarehouseService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


DataWarehouseServiceDep = Annotated[DataWarehouseService, Depends(data_warehouse_service)]

# Longer than any platform allows, so the platform check (with its own message) is what
# refuses a long name; this only keeps the payload bounded.
SchemaName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
Prefix = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


class LayerSchemas(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    staging: SchemaName = "staging"
    core: SchemaName = "core"
    mart: SchemaName = "mart"


class NamingRules(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_style: CaseStyle = Field("lower", description="Case of generated identifiers.")
    dimension_prefix: Prefix = "dim_"
    fact_prefix: Prefix = "fact_"
    bridge_prefix: Prefix = "bridge_"


class DateDimension(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_year: int = Field(2000, ge=1, le=9999)
    end_year: int = Field(2040, ge=1, le=9999)
    weekend_days: list[Weekday] = Field(["saturday", "sunday"], max_length=3)
    include_hijri: bool = False
    fiscal_year_start_month: int | None = Field(
        None,
        ge=1,
        le=12,
        description="First month of the fiscal year; null for no fiscal attributes.",
    )
    include_time_dimension: bool = False


class DataWarehouse(BaseModel):
    set_up: bool = Field(
        description="False until the step is done; then only this step (and Data Warehouse "
        "KPIs) is available, and the other fields are null."
    )
    id: uuid.UUID | None
    target_platform: TargetPlatform | None
    layer_schemas: LayerSchemas | None
    naming_rules: NamingRules | None
    date_dimension: DateDimension | None
    set_up_at: datetime | None
    updated_at: datetime | None
    version: int | None = Field(description="Send it back when editing; a stale one gets 409.")


class SetUpRequest(BaseModel):
    target_platform: TargetPlatform
    layer_schemas: LayerSchemas = LayerSchemas()
    naming_rules: NamingRules = NamingRules()
    date_dimension: DateDimension = DateDimension()


class UpdateRequest(BaseModel):
    version: int = Field(description="The `version` you last saw.")
    target_platform: TargetPlatform | None = Field(
        None, description="Changing it (not repeating the current one) is for owners only."
    )
    layer_schemas: LayerSchemas | None = None
    naming_rules: NamingRules | None = None
    date_dimension: DateDimension | None = None


class Platform(BaseModel):
    platform: TargetPlatform
    label: str
    schema_term: Literal["schema", "dataset"] = Field(
        description="What the platform calls a Layer's physical home."
    )
    max_identifier_length: int = Field(description="In bytes.")
    reserved_words: list[str] = Field(description="Lower-cased, sorted.")


class PlatformList(BaseModel):
    items: list[Platform]


def _out(warehouse: svc.DataWarehouse | None) -> DataWarehouse:
    if warehouse is None:
        return DataWarehouse(
            set_up=False,
            id=None,
            target_platform=None,
            layer_schemas=None,
            naming_rules=None,
            date_dimension=None,
            set_up_at=None,
            updated_at=None,
            version=None,
        )
    return DataWarehouse(
        set_up=True,
        id=warehouse.id,
        target_platform=warehouse.target_platform,
        layer_schemas=LayerSchemas.model_validate(warehouse.layer_schemas),
        naming_rules=NamingRules.model_validate(warehouse.naming_rules),
        date_dimension=DateDimension(
            **{
                **warehouse.date_dimension.__dict__,
                "weekend_days": list(warehouse.date_dimension.weekend_days),
            }
        ),
        set_up_at=warehouse.set_up_at,
        updated_at=warehouse.updated_at,
        version=warehouse.version,
    )


def _layer_schemas(body: LayerSchemas | None) -> svc.LayerSchemas | None:
    return svc.LayerSchemas(**body.model_dump()) if body is not None else None


def _naming_rules(body: NamingRules | None) -> svc.NamingRules | None:
    return svc.NamingRules(**body.model_dump()) if body is not None else None


def _date_dimension(body: DateDimension | None) -> svc.DateDimension | None:
    if body is None:
        return None
    return svc.DateDimension(**{**body.model_dump(), "weekend_days": tuple(body.weekend_days)})


@router.get("/data-warehouse/platforms", operation_id="listDataWarehousePlatforms")
def list_platforms(user: CurrentUser) -> PlatformList:
    """The target platforms and what each allows in an identifier, for the setup form
    (any signed-in user)."""
    return PlatformList(
        items=[
            Platform(
                platform=p.platform,
                label=p.label,
                schema_term=p.schema_term,
                max_identifier_length=p.max_identifier_length,
                reserved_words=sorted(p.reserved_words),
            )
            for p in (PLATFORM_PROFILES[name] for name in TARGET_PLATFORMS)
        ]
    )


@router.get("/workspaces/{workspace_id}/data-warehouse", operation_id="getDataWarehouse")
def get_data_warehouse(
    workspace_id: uuid.UUID, user: CurrentUser, warehouses: DataWarehouseServiceDep
) -> DataWarehouse:
    """The Data Warehouse's setup (any member). `set_up: false` until the step is done."""
    return _out(warehouses.get(user, workspace_id))


@router.post(
    "/workspaces/{workspace_id}/data-warehouse", operation_id="setUpDataWarehouse", status_code=201
)
def set_up_data_warehouse(
    workspace_id: uuid.UUID,
    body: SetUpRequest,
    user: CurrentUser,
    warehouses: DataWarehouseServiceDep,
) -> DataWarehouse:
    """Set up the Data Warehouse (editors and owners): target platform, a schema or
    dataset name per Layer, naming rules and date-dimension settings. 422
    `invalid_data_warehouse` if a name breaks the platform's rules; 409 `already_set_up`
    the second time."""
    return _out(
        warehouses.set_up(
            user,
            workspace_id,
            target_platform=body.target_platform,
            layer_schemas=svc.LayerSchemas(**body.layer_schemas.model_dump()),
            naming_rules=svc.NamingRules(**body.naming_rules.model_dump()),
            date_dimension=_date_dimension(body.date_dimension) or svc.DateDimension(),
        )
    )


@router.patch("/workspaces/{workspace_id}/data-warehouse", operation_id="updateDataWarehouse")
def update_data_warehouse(
    workspace_id: uuid.UUID,
    body: UpdateRequest,
    user: CurrentUser,
    warehouses: DataWarehouseServiceDep,
) -> DataWarehouse:
    """Change the setup (editors and owners). Fields left out stay as they are. Changing
    the target platform is for owners (403 for an editor). 404 `not_set_up` before setup."""
    return _out(
        warehouses.update(
            user,
            workspace_id,
            version=body.version,
            target_platform=body.target_platform,
            layer_schemas=_layer_schemas(body.layer_schemas),
            naming_rules=_naming_rules(body.naming_rules),
            date_dimension=_date_dimension(body.date_dimension),
        )
    )
