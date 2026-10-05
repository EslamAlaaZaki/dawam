from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .platforms import (
    SAFE_IDENTIFIER,
    TARGET_PLATFORMS,
    TargetPlatform,
    is_reserved_word,
    max_identifier_length,
)
from .tables import WORKSPACE_UNIQUE, DataWarehouseRecord

CaseStyle = Literal["lower", "upper"]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

MAX_PREFIX_LENGTH = 20
MAX_DATE_RANGE_YEARS = 200
_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


@dataclass(frozen=True)
class LayerSchemas:
    """The physical schema (dataset) each Layer's tables live in."""

    staging: str = "staging"
    core: str = "core"
    mart: str = "mart"

    def names(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class NamingRules:
    """How generated tables are named (spec story 96)."""

    case_style: CaseStyle = "lower"
    dimension_prefix: str = "dim_"
    fact_prefix: str = "fact_"
    bridge_prefix: str = "bridge_"


@dataclass(frozen=True)
class DateDimension:
    """Settings of the generated date dimension (spec story 90b)."""

    start_year: int = 2000
    end_year: int = 2040
    weekend_days: tuple[Weekday, ...] = ("saturday", "sunday")
    include_hijri: bool = False
    fiscal_year_start_month: int | None = None
    """1-12; ``None``: no fiscal-calendar attributes."""
    include_time_dimension: bool = False


@dataclass(frozen=True)
class DataWarehouse:
    id: uuid.UUID
    workspace_id: uuid.UUID
    target_platform: TargetPlatform
    layer_schemas: LayerSchemas
    naming_rules: NamingRules
    date_dimension: DateDimension
    set_up_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class _Settings:
    target_platform: TargetPlatform
    layer_schemas: LayerSchemas = field(default_factory=LayerSchemas)
    naming_rules: NamingRules = field(default_factory=NamingRules)
    date_dimension: DateDimension = field(default_factory=DateDimension)


def _invalid(field_name: str, message: str) -> ApiError:
    return ApiError(422, "invalid_data_warehouse", message, {"field": field_name})


def _validate(settings: _Settings) -> None:
    platform = settings.target_platform
    if platform not in TARGET_PLATFORMS:
        raise _invalid("target_platform", f"Unknown target platform {platform!r}.")
    limit = max_identifier_length(platform)
    seen: dict[str, str] = {}
    for layer, name in settings.layer_schemas.names().items():
        field_name = f"layer_schemas.{layer}"
        if not SAFE_IDENTIFIER.fullmatch(name):
            raise _invalid(
                field_name,
                "A schema name may use letters, digits and underscores only, "
                "and must not start with a digit.",
            )
        if len(name.encode()) > limit:
            raise _invalid(field_name, f"A name is at most {limit} bytes on {platform}.")
        if is_reserved_word(platform, name):
            raise _invalid(field_name, f"{name!r} is a reserved word on {platform}.")
        if name.lower() in seen:
            raise _invalid(
                field_name, f"The {layer} and {seen[name.lower()]} Layers need different names."
            )
        seen[name.lower()] = layer

    rules = settings.naming_rules
    prefixes = {
        "dimension_prefix": rules.dimension_prefix,
        "fact_prefix": rules.fact_prefix,
        "bridge_prefix": rules.bridge_prefix,
    }
    for field_name, prefix in prefixes.items():
        if prefix and (not _PREFIX.fullmatch(prefix) or len(prefix) > MAX_PREFIX_LENGTH):
            raise _invalid(
                f"naming_rules.{field_name}",
                f"A prefix is empty or starts with a letter, uses letters, digits and "
                f"underscores, and is at most {MAX_PREFIX_LENGTH} characters.",
            )
    used = [p.lower() for p in prefixes.values() if p]
    if len(used) != len(set(used)):
        raise _invalid("naming_rules", "Dimension, fact and bridge prefixes must differ.")

    dates = settings.date_dimension
    if dates.start_year > dates.end_year:
        raise _invalid("date_dimension.end_year", "The end year is before the start year.")
    if dates.end_year - dates.start_year + 1 > MAX_DATE_RANGE_YEARS:
        raise _invalid(
            "date_dimension.end_year", f"The range is at most {MAX_DATE_RANGE_YEARS} years."
        )
    if len(set(dates.weekend_days)) != len(dates.weekend_days) or len(dates.weekend_days) > 3:
        raise _invalid("date_dimension.weekend_days", "Pick up to three different weekend days.")
    month = dates.fiscal_year_start_month
    if month is not None and not 1 <= month <= 12:
        raise _invalid("date_dimension.fiscal_year_start_month", "The month is 1 to 12.")


def _view(record: DataWarehouseRecord) -> DataWarehouse:
    dates: dict[str, Any] = dict(record.date_dim_settings)
    dates["weekend_days"] = tuple(dates["weekend_days"])
    return DataWarehouse(
        id=record.id,
        workspace_id=record.workspace_id,
        target_platform=record.target_platform,  # type: ignore[arg-type]  # validated on write
        layer_schemas=LayerSchemas(**record.layer_physical_schemas),
        naming_rules=NamingRules(**record.naming_rules),
        date_dimension=DateDimension(**dates),
        set_up_at=record.set_up_at,
        updated_at=record.updated_at,
        version=record.version,
    )


def _write(record: DataWarehouseRecord, settings: _Settings) -> None:
    record.target_platform = settings.target_platform
    record.layer_physical_schemas = settings.layer_schemas.names()
    record.naming_rules = asdict(settings.naming_rules)
    dates = asdict(settings.date_dimension)
    dates["weekend_days"] = list(settings.date_dimension.weekend_days)
    record.date_dim_settings = dates


class DataWarehouseService:
    """The "Set up Data Warehouse" step and what it records. Every method that acts for
    a user authorizes through the Workspace policy first."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def get(self, user: User, workspace_id: uuid.UUID) -> DataWarehouse | None:
        """The Workspace's Data Warehouse, or ``None`` before it is set up (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        return self.settings_of(workspace_id)

    def settings_of(self, workspace_id: uuid.UUID) -> DataWarehouse | None:
        """The Data Warehouse of ``workspace_id`` without a permission check, for other
        modules that already authorized the caller (they need its platform and rules)."""
        with Session(self._engine) as db:
            record = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            return _view(record) if record is not None else None

    def set_up(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        target_platform: TargetPlatform,
        layer_schemas: LayerSchemas = LayerSchemas(),  # noqa: B008  (immutable)
        naming_rules: NamingRules = NamingRules(),  # noqa: B008
        date_dimension: DateDimension = DateDimension(),  # noqa: B008
    ) -> DataWarehouse:
        """Set the Data Warehouse up (editors and owners). 409 ``already_set_up`` if it is."""
        self._workspaces.authorize(user, Action.SET_UP_DATA_WAREHOUSE, workspace_id)
        settings = _Settings(target_platform, layer_schemas, naming_rules, date_dimension)
        _validate(settings)
        now = self._clock()
        record = DataWarehouseRecord(
            id=uuid.uuid4(), workspace_id=workspace_id, set_up_at=now, updated_at=now, version=1
        )
        _write(record, settings)
        try:
            with Session(self._engine) as db, db.begin():
                db.add(record)
                db.flush()
                return _view(record)
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == WORKSPACE_UNIQUE:
                raise ApiError(
                    409, "already_set_up", "This Workspace's Data Warehouse is already set up."
                ) from None
            raise

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        version: int,
        target_platform: TargetPlatform | None = None,
        layer_schemas: LayerSchemas | None = None,
        naming_rules: NamingRules | None = None,
        date_dimension: DateDimension | None = None,
    ) -> DataWarehouse:
        """Change the settings (``None``: leave as is). Editors and owners may, except
        that changing the platform is for owners (403 ``forbidden``). 404
        ``not_set_up`` before setup; 409 ``version_conflict`` if ``version`` is stale."""
        self._workspaces.authorize(user, Action.SET_UP_DATA_WAREHOUSE, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = db.scalars(
                sa.select(DataWarehouseRecord)
                .where(DataWarehouseRecord.workspace_id == workspace_id)
                .with_for_update()
            ).first()
            if record is None:
                raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
            if record.version != version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "Someone else changed the Data Warehouse since you loaded it. "
                    "Reload and try again.",
                    {"current_version": record.version},
                )
            current = _view(record)
            if target_platform is not None and target_platform != current.target_platform:
                self._workspaces.authorize(user, Action.CHANGE_DW_PLATFORM, workspace_id)
            settings = _Settings(
                target_platform or current.target_platform,
                layer_schemas or current.layer_schemas,
                naming_rules or current.naming_rules,
                date_dimension or current.date_dimension,
            )
            _validate(settings)
            _write(record, settings)
            record.version += 1
            record.updated_at = self._clock()
            db.flush()
            return _view(record)
