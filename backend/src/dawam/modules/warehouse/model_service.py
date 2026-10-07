"""The Core and Mart model editor (spec §6.9, stories 89-93a): the DW Schema's tables and
columns, edited by hand.

Every method authorizes through the workspaces policy first. Every change is audited
(``dw_table`` and ``dw_column`` entities) and, when a user asked for it, recorded in the
activity feed, in the transaction that makes it. Tables and columns carry a ``version``
(optimistic concurrency, spec §8.3): a stale one is 409 ``version_conflict``.

DAWAM maintains some structure itself: a dimension gets its surrogate key and an
unknown member (key ``-1``, configurable default values), a bridge gets its group key,
and a dimension that is, or has an attribute that is, SCD2 gets the housekeeping columns
(``is_system``: not edited or deleted by hand).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .calendar import date_columns, date_dimension_of, time_columns
from .mapping_service import purge_edges_reading
from .naming import NamingViolation, check_column_name, check_table_name
from .platforms import SAFE_IDENTIFIER, is_reserved_word, max_identifier_length
from .service import NamingRules
from .tables import (
    ADDITIVITIES,
    COLUMN_NAME_UNIQUE,
    DESCRIPTION_MAX_LENGTH,
    FACT_TYPES,
    GRAIN_MAX_LENGTH,
    NAME_MAX_LENGTH,
    TABLE_NAME_UNIQUE,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
)

MODEL_LAYERS = ("core", "mart")
"""The Layers edited by hand; Staging Tables come from the Source Schema."""
MODEL_KINDS = ("fact", "dimension", "bridge")
GENERATED_TABLES = ("date", "time")
"""The built-in generated dimensions (story 90b)."""
SCD_TYPES = (0, 1, 2)
UNKNOWN_MEMBER_KEY = -1

USER_ROLES = ("sk", "nk", "fk", "measure", "attribute", "degenerate_dimension", "audit")
"""The column roles a user sets; the SCD housekeeping roles belong to DAWAM."""
REFERENCEABLE_KINDS = ("dimension", "generated")
SEMANTIC_TYPE_MAX_LENGTH = 64
DEFAULT_VALUE_MAX_LENGTH = 200

NEUTRAL_TYPES = (
    "smallint",
    "integer",
    "bigint",
    "decimal",
    "float",
    "double",
    "boolean",
    "char",
    "string",
    "text",
    "binary",
    "date",
    "time",
    "timestamp",
    "timestamptz",
    "uuid",
    "json",
)
_LENGTH_TYPES = ("char", "string", "binary")
MAX_LENGTH = 1_000_000
MAX_PRECISION = 38

TABLE_FIELDS = (
    "layer",
    "name",
    "kind",
    "fact_type",
    "grain",
    "is_aggregate",
    "scd_type",
    "is_conformed",
    "unknown_member",
    "description",
)
COLUMN_FIELDS = (
    "name",
    "data_type",
    "is_nullable",
    "role",
    "additivity",
    "scd_type_override",
    "references_table_id",
    "role_name",
    "description",
    "semantic_type",
)

_HOUSEKEEPING: tuple[tuple[str, dict[str, Any], bool], ...] = (
    ("scd_valid_from", {"type": "timestamp"}, False),
    ("scd_valid_to", {"type": "timestamp"}, True),
    ("scd_current_flag", {"type": "boolean"}, False),
    ("row_hash", {"type": "string", "length": 64}, False),
)
"""Role (= column name), neutral type and nullability of each SCD2 housekeeping column."""


@dataclass(frozen=True)
class ModelColumn:
    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    ordinal: int
    data_type: dict[str, Any]
    is_nullable: bool
    role: str
    additivity: str | None
    scd_type_override: int | None
    references_table_id: uuid.UUID | None
    role_name: str | None
    description: str
    semantic_type: str | None
    is_system: bool
    version: int
    naming_violations: list[NamingViolation]
    review_flags: list[dict[str, str]]


@dataclass(frozen=True)
class ModelTableSummary:
    id: uuid.UUID
    layer: str
    name: str
    kind: str
    fact_type: str | None
    grain: str | None
    is_aggregate: bool
    scd_type: int | None
    is_conformed: bool
    description: str
    column_count: int
    version: int
    naming_violation_count: int


@dataclass(frozen=True)
class ModelTable:
    id: uuid.UUID
    layer: str
    name: str
    kind: str
    fact_type: str | None
    grain: str | None
    is_aggregate: bool
    scd_type: int | None
    is_conformed: bool
    unknown_member: dict[str, Any] | None
    description: str
    columns: list[ModelColumn]
    created_at: datetime
    updated_at: datetime
    version: int
    naming_violations: list[NamingViolation]
    review_flags: list[dict[str, str]]


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(422, "invalid_model", message, {"field": field})


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def _name_taken(what: str) -> ApiError:
    return ApiError(409, "name_taken", f"Another {what} already has this name.")


def _conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Someone else changed this since you loaded it. Reload and try again.",
        {"current_version": current},
    )


def _text(value: Any, field: str, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise _invalid(field, f"The {label} must be text.")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise _invalid(field, f"The {label} must be at most {max_length} characters.")
    return cleaned


def _identifier(platform: str, value: Any, field: str, label: str) -> str:
    name = _text(value, field, label, NAME_MAX_LENGTH)
    if not SAFE_IDENTIFIER.fullmatch(name):
        raise _invalid(
            field,
            f"A {label} may use letters, digits and underscores only, "
            "and must not start with a digit.",
        )
    limit = max_identifier_length(platform)  # type: ignore[arg-type]
    if len(name.encode()) > limit:
        raise _invalid(field, f"A {label} is at most {limit} bytes on {platform}.")
    if is_reserved_word(platform, name):  # type: ignore[arg-type]
        raise _invalid(field, f"{name!r} is a reserved word on {platform}.")
    return name


def neutral_type(value: Any) -> dict[str, Any]:
    """Validate a neutral data type and return it in its stored form."""
    if not isinstance(value, Mapping):
        raise _invalid("data_type", "The data type must have a type.")
    kind = value.get("type")
    if kind not in NEUTRAL_TYPES:
        raise _invalid("data_type", f"The type must be one of {', '.join(NEUTRAL_TYPES)}.")
    length, precision, scale = (value.get(k) for k in ("length", "precision", "scale"))
    if length is not None and kind not in _LENGTH_TYPES:
        raise _invalid("data_type", f"A {kind} has no length.")
    if (precision is not None or scale is not None) and kind != "decimal":
        raise _invalid("data_type", f"A {kind} has no precision or scale.")
    if length is not None and not 1 <= length <= MAX_LENGTH:
        raise _invalid("data_type", f"The length is 1 to {MAX_LENGTH}.")
    if kind == "decimal":
        if precision is not None and not 1 <= precision <= MAX_PRECISION:
            raise _invalid("data_type", f"The precision is 1 to {MAX_PRECISION}.")
        if scale is not None and not 0 <= scale <= (precision or MAX_PRECISION):
            raise _invalid("data_type", "The scale is 0 up to the precision.")
        if scale is not None and precision is None:
            raise _invalid("data_type", "A scale needs a precision.")
    return {"type": kind, "length": length, "precision": precision, "scale": scale}


def _table_snapshot(record: DwTableRecord) -> dict[str, Any]:
    return {f: _plain(getattr(record, f)) for f in TABLE_FIELDS}


def _column_snapshot(record: DwColumnRecord) -> dict[str, Any]:
    return {f: _plain(getattr(record, f)) for f in COLUMN_FIELDS}


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _rules(warehouse: DataWarehouseRecord) -> NamingRules:
    return NamingRules(**warehouse.naming_rules)


def _column_view(record: DwColumnRecord, layer: str, rules: NamingRules) -> ModelColumn:
    return ModelColumn(
        id=record.id,
        table_id=record.table_id,
        name=record.name,
        ordinal=record.ordinal,
        data_type=dict(record.data_type),
        is_nullable=record.is_nullable,
        role=record.role,
        additivity=record.additivity,
        scd_type_override=record.scd_type_override,
        references_table_id=record.references_table_id,
        role_name=record.role_name,
        description=record.description,
        semantic_type=record.semantic_type,
        is_system=record.is_system,
        version=record.version,
        naming_violations=check_column_name(
            rules, layer=layer, name=record.name, is_system=record.is_system
        ),
        review_flags=list(record.review_flags or []),
    )


def _table_view(
    record: DwTableRecord, columns: list[DwColumnRecord], rules: NamingRules
) -> ModelTable:
    return ModelTable(
        id=record.id,
        layer=record.layer,
        name=record.name,
        kind=record.kind,
        fact_type=record.fact_type,
        grain=record.grain,
        is_aggregate=record.is_aggregate,
        scd_type=record.scd_type,
        is_conformed=record.is_conformed,
        unknown_member=_plain(record.unknown_member) if record.unknown_member else None,
        description=record.description,
        columns=[
            _column_view(c, record.layer, rules) for c in sorted(columns, key=lambda c: c.ordinal)
        ],
        created_at=record.created_at,
        updated_at=record.updated_at,
        version=record.version,
        naming_violations=check_table_name(
            rules, layer=record.layer, kind=record.kind, name=record.name
        ),
        review_flags=list(record.review_flags or []),
    )


def _constraint(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


class ModelService:
    """The DW Schema's Core and Mart tables and columns."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    # --- reading ---------------------------------------------------------------------

    def list_tables(
        self, user: User, workspace_id: uuid.UUID, *, layer: str | None = None
    ) -> list[ModelTableSummary]:
        """The model's tables, by Layer then name (any member); none before setup."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id, required=False)
            if warehouse is None:
                return []
            count = (
                sa.select(sa.func.count())
                .where(DwColumnRecord.table_id == DwTableRecord.id)
                .scalar_subquery()
            )
            query = (
                sa.select(DwTableRecord, count)
                .where(DwTableRecord.data_warehouse_id == warehouse.id)
                .order_by(sa.func.lower(DwTableRecord.name), DwTableRecord.id)
            )
            if layer is not None:
                query = query.where(DwTableRecord.layer == layer)
            rows = db.execute(query).all()
        rules = _rules(warehouse)
        summaries = [
            ModelTableSummary(
                id=t.id,
                layer=t.layer,
                name=t.name,
                kind=t.kind,
                fact_type=t.fact_type,
                grain=t.grain,
                is_aggregate=t.is_aggregate,
                scd_type=t.scd_type,
                is_conformed=t.is_conformed,
                description=t.description,
                column_count=n,
                version=t.version,
                naming_violation_count=len(
                    check_table_name(rules, layer=t.layer, kind=t.kind, name=t.name)
                ),
            )
            for t, n in rows
        ]
        order = {"staging": 0, "core": 1, "mart": 2}
        return sorted(summaries, key=lambda s: order[s.layer])

    def get_table(self, user: User, workspace_id: uuid.UUID, table_id: uuid.UUID) -> ModelTable:
        """A table with its columns (any member). 404 ``not_found``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id, required=False)
            if warehouse is None:
                raise _not_found("Table")
            table = self._table(db, warehouse, table_id)
            return _table_view(table, self._columns(db, table.id), _rules(warehouse))

    # --- tables ----------------------------------------------------------------------

    def create_table(
        self, user: User, workspace_id: uuid.UUID, *, fields: Mapping[str, Any]
    ) -> ModelTable:
        """Create a Core or Mart fact, dimension or bridge. ``fields``: ``layer``,
        ``name``, ``kind`` and what the kind needs (a fact: ``grain`` and ``fact_type``).
        422 ``invalid_model``; 409 ``name_taken``; 404 ``not_set_up``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                warehouse = self._warehouse(db, workspace_id)
                assert warehouse is not None
                now = self._clock()
                table = self._build_table(db, warehouse, user, fields, now)
                db.add(table)
                db.flush()
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="dw_table",
                    entity_id=table.id,
                    old=None,
                    new=_table_snapshot(table),
                    at=now,
                )
                self._activity(db, user, workspace_id, "dw_table.created", table)
                if table.kind in ("dimension", "bridge"):
                    suffix = "_group_key" if table.kind == "bridge" else "_key"
                    # Shortened so the key fits the platform's identifier limit.
                    room = max_identifier_length(warehouse.target_platform) - len(suffix)  # type: ignore[arg-type]
                    self._add_column(
                        db,
                        user,
                        workspace_id,
                        table,
                        {
                            "name": f"{table.name[:room]}{suffix}",
                            "data_type": {"type": "bigint"},
                            "is_nullable": False,
                            "role": "sk",
                        },
                        now,
                        scaffold=True,
                    )
                self._sync_scd2(db, user, workspace_id, table, now)
                return _table_view(table, self._columns(db, table.id), _rules(warehouse))
        except IntegrityError as exc:
            raise self._integrity(exc) from None

    def create_generated_table(
        self, user: User, workspace_id: uuid.UUID, *, which: str, layer: str = "core"
    ) -> ModelTable:
        """Add the built-in ``date`` or ``time`` dimension (spec story 90b): a ``generated``
        table, conformed, whose columns follow the Data Warehouse's date-dimension settings
        (optional Hijri and fiscal columns). It needs no mapping. 422 ``invalid_model``;
        409 ``name_taken``; 404 ``not_set_up``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        if which not in GENERATED_TABLES:
            raise _invalid("kind", f"The generated table is one of {', '.join(GENERATED_TABLES)}.")
        if layer not in MODEL_LAYERS:
            raise _invalid("layer", "Core and Mart tables are modelled here; pick core or mart.")
        try:
            with Session(self._engine) as db, db.begin():
                warehouse = self._warehouse(db, workspace_id)
                assert warehouse is not None
                rules = _rules(warehouse)
                settings = date_dimension_of(warehouse.date_dim_settings)
                columns = date_columns(settings) if which == "date" else time_columns()
                name = (
                    f"{rules.dimension_prefix}{which}" if rules.dimension_prefix else f"{which}_dim"
                )
                if rules.case_style == "upper":
                    name = name.upper()
                self._ensure_table_name_free(db, warehouse, layer, name, None)
                now = self._clock()
                table = DwTableRecord(
                    id=uuid.uuid4(),
                    data_warehouse_id=warehouse.id,
                    layer=layer,
                    name=name,
                    kind="generated",
                    fact_type=None,
                    grain=None,
                    is_aggregate=False,
                    scd_type=None,
                    is_conformed=True,
                    unknown_member=None,
                    description=f"Generated {which} dimension; needs no mapping.",
                    created_by=user.id,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                db.add(table)
                db.flush()
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="dw_table",
                    entity_id=table.id,
                    old=None,
                    new=_table_snapshot(table),
                    at=now,
                )
                self._activity(db, user, workspace_id, "dw_table.created", table)
                for column in columns:
                    self._add_column(
                        db,
                        user,
                        workspace_id,
                        table,
                        {
                            "name": column.name,
                            "data_type": dict(column.data_type),
                            "is_nullable": column.is_nullable,
                            "role": column.role,
                        },
                        now,
                        scaffold=True,
                    )
                return _table_view(table, self._columns(db, table.id), rules)
        except IntegrityError as exc:
            raise self._integrity(exc) from None

    def update_table(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> ModelTable:
        """Change a table; ``changes`` holds only the fields to change (``name``,
        ``description``, ``grain``, ``fact_type``, ``is_aggregate``, ``scd_type``,
        ``is_conformed``, ``unknown_member_defaults``). A stale ``version`` is 409
        ``version_conflict``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                warehouse = self._warehouse(db, workspace_id)
                assert warehouse is not None
                table = self._table(db, warehouse, table_id, lock=True)
                if table.version != version:
                    raise _conflict(table.version)
                before = _table_snapshot(table)
                now = self._clock()
                columns = self._columns(db, table.id)
                self._apply_table(db, warehouse, table, columns, changes)
                db.flush()
                self._sync_scd2(db, user, workspace_id, table, now)
                self._finish_table_edit(db, user, workspace_id, table, before, now)
                return _table_view(table, self._columns(db, table.id), _rules(warehouse))
        except IntegrityError as exc:
            raise self._integrity(exc) from None

    def delete_table(self, user: User, workspace_id: uuid.UUID, table_id: uuid.UUID) -> None:
        """Delete a table and its columns. 409 ``table_referenced`` while another table's
        foreign key points at it."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db, db.begin():
            warehouse = self._warehouse(db, workspace_id)
            assert warehouse is not None
            table = self._table(db, warehouse, table_id, lock=True)
            referencing = db.scalar(
                sa.select(sa.func.count())
                .select_from(DwColumnRecord)
                .where(
                    DwColumnRecord.references_table_id == table.id,
                    DwColumnRecord.table_id != table.id,
                )
            )
            if referencing:
                raise ApiError(
                    409,
                    "table_referenced",
                    "Other tables have foreign keys to this one. Remove those first.",
                    {"referencing_columns": referencing},
                )
            now = self._clock()
            purge_edges_reading(db, [c.id for c in self._columns(db, table.id)])
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="dw_table",
                entity_id=table.id,
                old=_table_snapshot(table),
                new=None,
                at=now,
            )
            self._activity(db, user, workspace_id, "dw_table.deleted", table)
            db.delete(table)

    # --- columns ---------------------------------------------------------------------

    def add_column(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        *,
        fields: Mapping[str, Any],
    ) -> ModelColumn:
        """Add a column to a table. ``fields``: ``name``, ``data_type`` and ``role`` and,
        as the role needs, ``additivity``, ``references_table_id``, ``role_name``,
        ``scd_type_override``; also ``is_nullable``, ``description``, ``semantic_type``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                warehouse = self._warehouse(db, workspace_id)
                assert warehouse is not None
                table = self._table(db, warehouse, table_id, lock=True)
                now = self._clock()
                column = self._add_column(db, user, workspace_id, table, fields, now)
                self._sync_scd2(db, user, workspace_id, table, now)
                return _column_view(column, table.layer, _rules(warehouse))
        except IntegrityError as exc:
            raise self._integrity(exc) from None

    def update_column(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> ModelColumn:
        """Change a column; ``changes`` holds only the fields to change. A stale
        ``version`` is 409 ``version_conflict``; a housekeeping column is 422
        ``system_column``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        try:
            with Session(self._engine) as db, db.begin():
                warehouse = self._warehouse(db, workspace_id)
                assert warehouse is not None
                table = self._table(db, warehouse, table_id, lock=True)
                column = self._column(db, table, column_id)
                if column.is_system:
                    raise _system_column()
                if column.version != version:
                    raise _conflict(column.version)
                before_table = _table_snapshot(table)
                before = _column_snapshot(column)
                now = self._clock()
                siblings = [c for c in self._columns(db, table.id) if c.id != column.id]
                final = {**before, **self._column_fields(warehouse, changes, partial=True)}
                final["references_table_id"] = (
                    _uuid(final["references_table_id"]) if final["references_table_id"] else None
                )
                self._check_column(db, warehouse, table, siblings, final)
                for field in COLUMN_FIELDS:
                    setattr(column, field, final[field])
                after = _column_snapshot(column)
                changed = [f for f in COLUMN_FIELDS if before[f] != after[f]]
                if changed:
                    if "name" in changed:
                        self._rename_default(table, before["name"], column.name)
                    column.version += 1
                    column.updated_at = now
                    db.flush()
                    record_audit(
                        db,
                        workspace_id=workspace_id,
                        actor_id=user.id,
                        entity_type="dw_column",
                        entity_id=column.id,
                        old={f: before[f] for f in changed},
                        new={f: after[f] for f in changed},
                        at=now,
                    )
                    self._activity(
                        db,
                        user,
                        workspace_id,
                        "dw_column.updated",
                        column,
                        table=table,
                        details={"fields": changed},
                    )
                    self._sync_scd2(db, user, workspace_id, table, now)
                    self._finish_table_edit(db, user, workspace_id, table, before_table, now)
                return _column_view(column, table.layer, _rules(warehouse))
        except IntegrityError as exc:
            raise self._integrity(exc) from None

    def delete_column(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        column_id: uuid.UUID,
    ) -> None:
        """Delete a column (not a housekeeping one: 422 ``system_column``)."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db, db.begin():
            warehouse = self._warehouse(db, workspace_id)
            assert warehouse is not None
            table = self._table(db, warehouse, table_id, lock=True)
            column = self._column(db, table, column_id)
            if column.is_system:
                raise _system_column()
            before_table = _table_snapshot(table)
            now = self._clock()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="dw_column",
                entity_id=column.id,
                old=_column_snapshot(column),
                new=None,
                at=now,
            )
            self._activity(db, user, workspace_id, "dw_column.deleted", column, table=table)
            name = column.name
            purge_edges_reading(db, [column.id])
            db.delete(column)
            db.flush()
            self._rename_default(table, name, None)
            for ordinal, remaining in enumerate(self._columns(db, table.id), start=1):
                remaining.ordinal = ordinal
            self._sync_scd2(db, user, workspace_id, table, now)
            self._finish_table_edit(db, user, workspace_id, table, before_table, now)

    # --- loading ---------------------------------------------------------------------

    def _warehouse(
        self, db: Session, workspace_id: uuid.UUID, *, required: bool = True
    ) -> DataWarehouseRecord | None:
        warehouse = db.scalars(
            sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
        ).first()
        if warehouse is None and required:
            raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
        return warehouse

    def _table(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        table_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> DwTableRecord:
        query = sa.select(DwTableRecord).where(
            DwTableRecord.id == table_id, DwTableRecord.data_warehouse_id == warehouse.id
        )
        if lock:
            query = query.with_for_update()
        table = db.scalars(query).first()
        if table is None:
            raise _not_found("Table")
        return table

    def _columns(self, db: Session, table_id: uuid.UUID) -> list[DwColumnRecord]:
        return list(
            db.scalars(
                sa.select(DwColumnRecord)
                .where(DwColumnRecord.table_id == table_id)
                .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
            )
        )

    def _column(self, db: Session, table: DwTableRecord, column_id: uuid.UUID) -> DwColumnRecord:
        column = db.scalars(
            sa.select(DwColumnRecord)
            .where(DwColumnRecord.id == column_id, DwColumnRecord.table_id == table.id)
            .with_for_update()
        ).first()
        if column is None:
            raise _not_found("Column")
        return column

    # --- tables: rules ---------------------------------------------------------------

    def _build_table(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        user: User,
        fields: Mapping[str, Any],
        now: datetime,
    ) -> DwTableRecord:
        layer, kind = fields.get("layer"), fields.get("kind")
        if layer not in MODEL_LAYERS:
            raise _invalid("layer", "Core and Mart tables are modelled here; pick core or mart.")
        if kind not in MODEL_KINDS:
            raise _invalid("kind", f"The kind must be one of {', '.join(MODEL_KINDS)}.")
        name = _identifier(warehouse.target_platform, fields.get("name"), "name", "table name")
        self._ensure_table_name_free(db, warehouse, layer, name, None)
        table = DwTableRecord(
            id=uuid.uuid4(),
            data_warehouse_id=warehouse.id,
            layer=layer,
            name=name,
            kind=kind,
            fact_type=None,
            grain=None,
            is_aggregate=False,
            scd_type=None,
            is_conformed=False,
            unknown_member=None,
            description="",
            created_by=user.id,
            created_at=now,
            updated_at=now,
            version=1,
        )
        if kind == "fact":
            table.grain = _grain(fields.get("grain"))
            table.fact_type = _fact_type(fields.get("fact_type"))
        else:
            for field in ("grain", "fact_type", "is_aggregate"):
                if fields.get(field) not in (None, False):
                    raise _invalid(field, f"Only a fact has a {field.replace('_', ' ')}.")
        if kind == "dimension":
            table.scd_type = _scd_type(fields.get("scd_type", 1), "scd_type")
            table.unknown_member = {"surrogate_key": UNKNOWN_MEMBER_KEY, "defaults": {}}
            table.is_conformed = bool(fields.get("is_conformed", False))
        else:
            if fields.get("scd_type") is not None:
                raise _invalid("scd_type", "Only a dimension has an SCD type.")
            if fields.get("is_conformed"):
                raise _invalid("is_conformed", "Only a dimension can be conformed.")
        if kind == "fact":
            table.is_aggregate = bool(fields.get("is_aggregate", False))
        if fields.get("description") is not None:
            table.description = _text(
                fields["description"], "description", "description", DESCRIPTION_MAX_LENGTH
            )
        if fields.get("unknown_member_defaults"):
            raise _invalid(
                "unknown_member_defaults", "Add the columns first, then set their defaults."
            )
        return table

    def _apply_table(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        table: DwTableRecord,
        columns: list[DwColumnRecord],
        changes: Mapping[str, Any],
    ) -> None:
        for field, value in changes.items():
            if field == "name":
                name = _identifier(warehouse.target_platform, value, "name", "table name")
                self._ensure_table_name_free(db, warehouse, table.layer, name, table.id)
                table.name = name
            elif field == "description":
                table.description = _text(value, field, "description", DESCRIPTION_MAX_LENGTH)
            elif field in ("grain", "fact_type", "is_aggregate"):
                if table.kind != "fact":
                    raise _invalid(field, f"Only a fact has a {field.replace('_', ' ')}.")
                if field == "grain":
                    table.grain = _grain(value)
                elif field == "fact_type":
                    table.fact_type = _fact_type(value)
                else:
                    table.is_aggregate = bool(value)
            elif field in ("scd_type", "is_conformed", "unknown_member_defaults"):
                if table.kind != "dimension":
                    raise _invalid(field, "Only a dimension has this.")
                if field == "scd_type":
                    table.scd_type = _scd_type(value, field)
                elif field == "is_conformed":
                    if not value:
                        self._ensure_not_used_from_mart(db, table)
                    table.is_conformed = bool(value)
                else:
                    table.unknown_member = {
                        "surrogate_key": UNKNOWN_MEMBER_KEY,
                        "defaults": self._defaults(columns, value),
                    }
            else:  # pragma: no cover - a programming error
                raise ValueError(f"cannot change table field {field!r}")

    def _ensure_table_name_free(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        layer: str,
        name: str,
        own_id: uuid.UUID | None,
    ) -> None:
        taken = db.scalar(
            sa.select(sa.func.count())
            .select_from(DwTableRecord)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer == layer,
                sa.func.lower(DwTableRecord.name) == name.lower(),
                DwTableRecord.id != (own_id or uuid.UUID(int=0)),
            )
        )
        if taken:
            raise _name_taken("table in this Layer")

    def _ensure_not_used_from_mart(self, db: Session, table: DwTableRecord) -> None:
        used = db.scalar(
            sa.select(sa.func.count())
            .select_from(DwColumnRecord)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .where(
                DwColumnRecord.references_table_id == table.id,
                DwTableRecord.layer != table.layer,
            )
        )
        if used:
            raise ApiError(
                409,
                "table_referenced",
                "Tables in another Layer reference this dimension as a conformed one.",
                {"referencing_columns": used},
            )

    def _defaults(self, columns: list[DwColumnRecord], value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise _invalid("unknown_member_defaults", "The defaults map a column to its value.")
        by_name = {c.name.lower(): c for c in columns}
        defaults: dict[str, Any] = {}
        for key, default in value.items():
            column = by_name.get(str(key).lower())
            if column is None or column.is_system or column.role == "sk":
                raise _invalid(
                    "unknown_member_defaults",
                    f"{key!r} is not a column whose unknown-member value can be set.",
                )
            if default is not None and not isinstance(default, str | int | float | bool):
                raise _invalid("unknown_member_defaults", "A default is a single value.")
            if isinstance(default, str) and len(default) > DEFAULT_VALUE_MAX_LENGTH:
                raise _invalid(
                    "unknown_member_defaults",
                    f"A default is at most {DEFAULT_VALUE_MAX_LENGTH} characters.",
                )
            defaults[column.name] = default
        return defaults

    def _rename_default(self, table: DwTableRecord, old: str, new: str | None) -> None:
        """Keep the unknown member's defaults pointing at existing columns."""
        member = table.unknown_member
        if not member or old not in member.get("defaults", {}):
            return
        defaults = dict(member["defaults"])
        value = defaults.pop(old)
        if new is not None:
            defaults[new] = value
        table.unknown_member = {**member, "defaults": defaults}

    def _finish_table_edit(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        table: DwTableRecord,
        before: dict[str, Any],
        now: datetime,
    ) -> None:
        after = _table_snapshot(table)
        changed = [f for f in TABLE_FIELDS if before[f] != after[f]]
        if not changed:
            return
        table.version += 1
        table.updated_at = now
        db.flush()
        record_audit(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            entity_type="dw_table",
            entity_id=table.id,
            old={f: before[f] for f in changed},
            new={f: after[f] for f in changed},
            at=now,
        )
        self._activity(
            db, user, workspace_id, "dw_table.updated", table, details={"fields": changed}
        )

    # --- columns: rules --------------------------------------------------------------

    def _column_fields(
        self, warehouse: DataWarehouseRecord, fields: Mapping[str, Any], *, partial: bool
    ) -> dict[str, Any]:
        """The given column fields, each checked on its own."""
        out: dict[str, Any] = {}
        platform = warehouse.target_platform
        for field, value in fields.items():
            if field == "name":
                out[field] = _identifier(platform, value, field, "column name")
            elif field == "data_type":
                out[field] = neutral_type(value)
            elif field == "is_nullable":
                out[field] = bool(value)
            elif field == "role":
                if value not in USER_ROLES:
                    raise _invalid(field, f"The role must be one of {', '.join(USER_ROLES)}.")
                out[field] = value
            elif field == "additivity":
                if value is not None and value not in ADDITIVITIES:
                    raise _invalid(
                        field, f"The additivity must be one of {', '.join(ADDITIVITIES)}."
                    )
                out[field] = value
            elif field == "scd_type_override":
                out[field] = None if value is None else _scd_type(value, field)
            elif field == "references_table_id":
                out[field] = value
            elif field == "role_name":
                out[field] = (
                    None if value is None else _identifier(platform, value, field, "role name")
                )
            elif field == "description":
                out[field] = _text(value, field, "description", DESCRIPTION_MAX_LENGTH)
            elif field == "semantic_type":
                out[field] = (
                    None
                    if value is None
                    else _text(value, field, "semantic type", SEMANTIC_TYPE_MAX_LENGTH) or None
                )
            else:  # pragma: no cover - a programming error
                raise ValueError(f"cannot change column field {field!r}")
        if not partial:
            for required in ("name", "data_type", "role"):
                if required not in out:
                    raise _invalid(required, f"The {required.replace('_', ' ')} is required.")
        return out

    def _check_column(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        table: DwTableRecord,
        siblings: list[DwColumnRecord],
        final: dict[str, Any],
    ) -> None:
        """Rules that look at the whole column and its table."""
        role = final["role"]
        if any(s.name.lower() == final["name"].lower() for s in siblings):
            raise _name_taken("column in this table")
        if role in ("measure", "degenerate_dimension") and table.kind != "fact":
            raise _invalid("role", f"Only a fact has a {role.replace('_', ' ')} column.")
        if final["additivity"] is not None and role != "measure":
            raise _invalid("additivity", "Only a measure has an additivity.")
        if final["scd_type_override"] is not None and not (
            table.kind == "dimension" and role == "attribute"
        ):
            raise _invalid(
                "scd_type_override", "Only a dimension's attributes have an SCD type override."
            )
        if role != "fk":
            if final["references_table_id"] is not None:
                raise _invalid("references_table_id", "Only a foreign key references a table.")
            if final["role_name"] is not None:
                raise _invalid("role_name", "Only a foreign key has a role name.")
            return
        if final["references_table_id"] is None:
            raise _invalid("references_table_id", "A foreign key must reference a dimension.")
        target = db.scalars(
            sa.select(DwTableRecord).where(
                DwTableRecord.id == final["references_table_id"],
                DwTableRecord.data_warehouse_id == warehouse.id,
            )
        ).first()
        if target is None:
            raise _invalid("references_table_id", "The referenced table does not exist.")
        if target.id == table.id:
            raise _invalid("references_table_id", "A table cannot reference itself.")
        if target.kind not in REFERENCEABLE_KINDS:
            raise _invalid("references_table_id", "A foreign key references a dimension.")
        if target.layer != table.layer and not (
            table.layer == "mart" and target.layer == "core" and target.is_conformed
        ):
            raise _invalid(
                "references_table_id",
                "A table references dimensions of its own Layer; a Mart table may also "
                "reference Core conformed dimensions.",
            )
        same_role = [
            s
            for s in siblings
            if s.role == "fk"
            and s.references_table_id == target.id
            and s.role_name == final["role_name"]
        ]
        if same_role:
            raise _invalid(
                "role_name",
                "This table already has a foreign key to that dimension"
                + (
                    f" in the role {final['role_name']!r}."
                    if final["role_name"]
                    else "; give each a role name."
                ),
            )

    def _add_column(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        table: DwTableRecord,
        fields: Mapping[str, Any],
        now: datetime,
        *,
        scaffold: bool = False,
    ) -> DwColumnRecord:
        warehouse = db.get(DataWarehouseRecord, table.data_warehouse_id)
        assert warehouse is not None
        given = self._column_fields(warehouse, fields, partial=False)
        final: dict[str, Any] = {
            "additivity": None,
            "scd_type_override": None,
            "references_table_id": None,
            "role_name": None,
            "description": "",
            "semantic_type": None,
            "is_nullable": True,
            **given,
        }
        if final["references_table_id"] is not None:
            final["references_table_id"] = _uuid(final["references_table_id"])
        existing = self._columns(db, table.id)
        self._check_column(db, warehouse, table, existing, final)
        column = DwColumnRecord(
            id=uuid.uuid4(),
            table_id=table.id,
            ordinal=len(existing) + 1,
            is_system=False,
            created_at=now,
            updated_at=now,
            version=1,
            **final,
        )
        db.add(column)
        db.flush()
        self._audit_column_created(db, user, workspace_id, column, now)
        if not scaffold:
            self._activity(db, user, workspace_id, "dw_column.created", column, table=table)
        return column

    def _audit_column_created(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        column: DwColumnRecord,
        now: datetime,
    ) -> None:
        record_audit(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            entity_type="dw_column",
            entity_id=column.id,
            old=None,
            new=_column_snapshot(column),
            at=now,
        )

    # --- SCD2 housekeeping -----------------------------------------------------------

    def _sync_scd2(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        table: DwTableRecord,
        now: datetime,
    ) -> None:
        """A dimension that is SCD2, or has an SCD2 attribute, carries the housekeeping
        columns; any other table carries none of them."""
        columns = self._columns(db, table.id)
        needed = table.kind == "dimension" and (
            table.scd_type == 2 or any(c.scd_type_override == 2 for c in columns)
        )
        present = {c.role: c for c in columns if c.is_system}
        if needed:
            taken = {c.name.lower() for c in columns}
            for name, data_type, nullable in _HOUSEKEEPING:
                if name in present:
                    continue
                if name in taken:
                    raise _invalid(
                        "scd_type",
                        f"A column named {name!r} already exists; rename it before "
                        "making this dimension SCD2.",
                    )
                column = DwColumnRecord(
                    id=uuid.uuid4(),
                    table_id=table.id,
                    name=name,
                    ordinal=len(columns) + 1,
                    data_type={"length": None, "precision": None, "scale": None, **data_type},
                    is_nullable=nullable,
                    role=name,
                    additivity=None,
                    scd_type_override=None,
                    references_table_id=None,
                    role_name=None,
                    description="",
                    semantic_type=None,
                    is_system=True,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                db.add(column)
                columns.append(column)
                db.flush()
                self._audit_column_created(db, user, workspace_id, column, now)
        else:
            for column in present.values():
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="dw_column",
                    entity_id=column.id,
                    old=_column_snapshot(column),
                    new=None,
                    at=now,
                )
                db.delete(column)
            if present:
                db.flush()
                for ordinal, remaining in enumerate(self._columns(db, table.id), start=1):
                    remaining.ordinal = ordinal

    # --- bookkeeping -----------------------------------------------------------------

    def _activity(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        verb: str,
        record: DwTableRecord | DwColumnRecord,
        *,
        table: DwTableRecord | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        owner = table or record
        assert isinstance(owner, DwTableRecord)
        record_activity(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            verb=verb,
            object_type=verb.split(".")[0],
            object_id=record.id,
            object_label=record.name,
            details={"layer": owner.layer, "table_id": str(owner.id), **(details or {})},
            at=self._clock(),
        )

    def _integrity(self, exc: IntegrityError) -> Exception:
        constraint = _constraint(exc)
        if constraint == TABLE_NAME_UNIQUE:
            return _name_taken("table in this Layer")
        if constraint == COLUMN_NAME_UNIQUE:
            return _name_taken("column in this table")
        return exc


def _system_column() -> ApiError:
    return ApiError(
        422,
        "system_column",
        "DAWAM maintains this column (SCD2 housekeeping); change the dimension's SCD type instead.",
        {"field": "id"},
    )


def _uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise _invalid("references_table_id", "The referenced table does not exist.") from None


def _grain(value: Any) -> str:
    grain = _text(value, "grain", "grain", GRAIN_MAX_LENGTH) if value is not None else ""
    if not grain:
        raise _invalid("grain", "A fact needs a grain: what one row stands for.")
    return grain


def _fact_type(value: Any) -> str:
    if value not in FACT_TYPES:
        raise _invalid("fact_type", f"A fact needs a fact type: {', '.join(FACT_TYPES)}.")
    return value


def _scd_type(value: Any, field: str) -> int:
    if isinstance(value, bool) or value not in SCD_TYPES:
        raise _invalid(field, "The SCD type is 0, 1 or 2.")
    return value
