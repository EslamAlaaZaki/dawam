"""System Code and target platform changes as Change Sets (spec §6.7, §6.9, stories 39, 87).

Both changes are owner-only and produce **one** Change Set each, so an owner sees and accepts
everything the change touches together:

- ``plan_system_code_change`` (origin ``system_code_change``): the code itself
  (``source_system``), the rename of every Staging Table named after the old code
  (``stg_<code>_...``, kept unique and within the platform's identifier limit), and the
  ``source_system`` audit column's constant. The Core and Mart SQL that reads a renamed
  Staging Table follows through the ordinary propagation Change Set that accepting a rename
  triggers (``origin`` ``propagation``), exactly as for any other rename.
- ``plan_platform_change`` (origin ``platform_change``): the platform itself
  (``data_warehouse``; it is structural, so accepting it returns an approved Core and Mart to
  draft), the Staging Table column types translated again for the new platform, and the stored
  SQL of every Core and Mart mapping and branch transpiled to the new dialect. SQL sqlglot
  cannot translate stays as it is and gets a ``manual_review`` item that names why. The kpis
  module adds the KPI formulas the same way.

``DataWarehouseHandler``, ``TranspiledMappingHandler`` and ``ManualReviewHandler`` are the
engine's handlers for the ``data_warehouse``, ``column_mapping_sql`` and ``manual_review``
items.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import AppliedChange, ProposedItem
from dawam.modules.sources import StagingSystem
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .platforms import TARGET_PLATFORMS, TargetPlatform
from .propagation import same_type
from .propagation_handlers import ColumnMappingHandler
from .service import NamingRules
from .sql_transpile import FragmentKind, Transpiled, transpile
from .staging_naming import TYPE_FLAGS, NameInput, table_names
from .staging_types import translate_type
from .tables import (
    NAME_MAX_LENGTH,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

DATA_WAREHOUSE = "data_warehouse"
MANUAL_REVIEW = "manual_review"
PLATFORM_KEY = "platform"
CODE_KEY = "code"
MODEL_LAYERS = ("core", "mart")


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


@dataclass(frozen=True)
class ManualNote:
    """Stored SQL a platform change could not translate."""

    object_type: str
    """``column_mapping``, ``mapping_branch`` or ``kpi``."""
    object_id: uuid.UUID
    label: str
    reason: str


@dataclass
class PlatformPlan:
    items: list[ProposedItem] = field(default_factory=list)
    manual: list[ManualNote] = field(default_factory=list)


def manual_item(note: ManualNote, sql: str, *, key: str, depends_on: Sequence[str]) -> ProposedItem:
    """The item that flags untranslatable SQL: accepting it records the flag in the audit
    trail and leaves the SQL as it is."""
    return ProposedItem(
        key=key,
        object_type=MANUAL_REVIEW,
        operation="update",
        object_id=note.object_id,
        label=f"[manual] {note.label}",
        payload={"target_type": note.object_type, "reason": note.reason, "sql": sql},
        base_values={},
        depends_on=depends_on,
    )


def _dicts(flags: Sequence[Any]) -> list[dict[str, str]]:
    return [{"code": f.code, "message": f.message} for f in flags]


# -- System Code ----------------------------------------------------------------------------


def plan_system_code_change(
    db: Session, *, warehouse: DataWarehouseRecord | None, system: StagingSystem, code: str
) -> list[ProposedItem]:
    """The items of a System Code change of ``system`` to ``code``."""
    items = [
        ProposedItem(
            key=CODE_KEY,
            object_type="source_system",
            operation="update",
            object_id=system.id,
            label=f"{system.code} to {code}",
            payload={"code": code},
        )
    ]
    if warehouse is None:
        return items
    platform: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
    source_tables = {t.id: t for t in system.tables}
    staged = list(
        db.scalars(
            sa.select(DwTableRecord)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer == "staging",
                DwTableRecord.source_table_id.in_(list(source_tables)),
            )
            .order_by(DwTableRecord.name)
        )
    )
    prefix = f"stg_{system.code.lower()}_"
    renaming = [
        t
        for t in staged
        if t.name.lower().startswith(prefix) and "name" not in (t.edited_fields or [])
    ]
    renaming_ids = {t.id for t in renaming}
    taken = set(
        db.scalars(
            sa.select(sa.func.lower(DwTableRecord.name)).where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer == "staging",
                DwTableRecord.id.not_in(renaming_ids) if renaming_ids else sa.true(),
            )
        )
    )
    named = table_names(
        platform,
        tables=[
            (
                code,
                NameInput(str(source_tables[t.source_table_id].db_schema_id), schema_name),
                NameInput(
                    str(t.source_table_id),
                    source_tables[t.source_table_id].name,
                    source_tables[t.source_table_id].placeholder_no,
                ),
            )
            for t in renaming
            if t.source_table_id is not None
            for schema_name in (source_tables[t.source_table_id].db_schema,)
        ],
        taken=taken,
        max_length=NAME_MAX_LENGTH,
    )
    for table in renaming:
        new = named.get(str(table.source_table_id))
        if new is None or new.name == table.name:
            continue
        items.append(
            ProposedItem(
                key=f"rt:{table.id}",
                object_type="staging_table",
                operation="update",
                object_id=table.id,
                label=f"{table.name} to {new.name}",
                payload={"name": new.name, "review_flags": _dicts(new.flags)},
                depends_on=(CODE_KEY,),
            )
        )
    audit_name = NamingRules(**warehouse.naming_rules).source_system_column
    audit_columns = db.execute(
        sa.select(DwColumnRecord, DwTableRecord)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            DwColumnRecord.table_id.in_([t.id for t in staged]),
            DwColumnRecord.role == "audit",
            DwColumnRecord.name == audit_name,
        )
    )
    constant = "'" + code.replace("'", "''") + "'"
    for column, table in audit_columns:
        items.append(
            ProposedItem(
                key=f"ac:{column.id}",
                object_type="staging_column",
                operation="update",
                object_id=column.id,
                label=f"{table.name}.{column.name}",
                payload={"sql_expression": constant},
                depends_on=(CODE_KEY,),
            )
        )
    return items


# -- target platform ------------------------------------------------------------------------


def plan_platform_change(
    db: Session,
    *,
    warehouse: DataWarehouseRecord,
    systems: Sequence[StagingSystem],
    target: TargetPlatform,
) -> PlatformPlan:
    """The items of a change of the Data Warehouse's platform to ``target``. Items other than
    the platform depend on it, so they apply in the new dialect."""
    current: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
    plan = PlatformPlan()
    plan.items.append(
        ProposedItem(
            key=PLATFORM_KEY,
            object_type=DATA_WAREHOUSE,
            operation="update",
            object_id=warehouse.id,
            label=f"{current} to {target}",
            payload={"target_platform": target},
        )
    )
    _retype_staging(db, warehouse, systems, target, plan)
    _transpile_mappings(db, warehouse, current, target, plan)
    _transpile_branches(db, warehouse, current, target, plan)
    return plan


def _retype_staging(
    db: Session,
    warehouse: DataWarehouseRecord,
    systems: Sequence[StagingSystem],
    target: TargetPlatform,
    plan: PlatformPlan,
) -> None:
    source_columns = {
        c.id: (system, c) for system in systems for t in system.tables for c in t.columns
    }
    columns = db.execute(
        sa.select(DwColumnRecord, DwTableRecord)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer == "staging",
            DwColumnRecord.source_column_id.is_not(None),
        )
        .order_by(DwTableRecord.name, DwColumnRecord.ordinal)
    )
    for column, table in columns:
        found = source_columns.get(column.source_column_id)  # type: ignore[arg-type]
        if found is None:
            continue
        system, source = found
        translated = translate_type(target, source.data_type, engine=system.engine)
        flags = [f for f in column.review_flags if f.get("code") not in TYPE_FLAGS]
        flags += _dicts(translated.flags)
        if same_type(translated.data_type, column.data_type) and flags == column.review_flags:
            continue
        plan.items.append(
            ProposedItem(
                key=f"ct:{column.id}",
                object_type="staging_column",
                operation="update",
                object_id=column.id,
                label=f"{table.name}.{column.name}",
                payload={"data_type": translated.data_type, "review_flags": flags},
                depends_on=(PLATFORM_KEY,),
                is_conflict="data_type" in (column.edited_fields or []),
            )
        )


def _transpile_mappings(
    db: Session,
    warehouse: DataWarehouseRecord,
    current: TargetPlatform,
    target: TargetPlatform,
    plan: PlatformPlan,
) -> None:
    rows = db.execute(
        sa.select(ColumnMappingRecord, DwColumnRecord, DwTableRecord)
        .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
        .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer.in_(MODEL_LAYERS),
            ColumnMappingRecord.sql_expression != "",
        )
        .order_by(DwTableRecord.layer, DwTableRecord.name, DwColumnRecord.ordinal)
    )
    for mapping, column, table in rows:
        label = f"{table.name}.{column.name}"
        result = transpile("expression", mapping.sql_expression, current, target)
        if result.sql is None:
            note = ManualNote("column_mapping", mapping.id, label, result.manual or "")
            plan.manual.append(note)
            plan.items.append(
                manual_item(
                    note, mapping.sql_expression, key=f"mm:{mapping.id}", depends_on=(PLATFORM_KEY,)
                )
            )
        elif result.changed:
            plan.items.append(
                ProposedItem(
                    key=f"cm:{mapping.id}",
                    object_type="column_mapping_sql",
                    operation="update",
                    object_id=mapping.id,
                    label=label,
                    payload={"sql_expression": result.sql},
                    depends_on=(PLATFORM_KEY,),
                )
            )


BRANCH_KINDS: dict[str, FragmentKind] = {
    "driving_input": "table",
    "joins": "joins",
    "filters": "filter",
    "group_by": "group_by",
    "having": "having",
}


def _transpile_branches(
    db: Session,
    warehouse: DataWarehouseRecord,
    current: TargetPlatform,
    target: TargetPlatform,
    plan: PlatformPlan,
) -> None:
    rows = db.execute(
        sa.select(MappingBranchRecord, DwTableRecord)
        .join(TableMappingRecord, TableMappingRecord.id == MappingBranchRecord.table_mapping_id)
        .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id, DwTableRecord.layer.in_(MODEL_LAYERS)
        )
        .order_by(DwTableRecord.layer, DwTableRecord.name, MappingBranchRecord.ordinal)
    )
    for branch, table in rows:
        label = f"{table.name}: {branch.name}"
        changes: dict[str, str] = {}
        for part, kind in BRANCH_KINDS.items():
            text = getattr(branch, part)
            if not text or not text.strip():
                continue
            result: Transpiled = transpile(kind, text, current, target)
            if result.sql is None:
                note = ManualNote(
                    "mapping_branch", branch.id, f"{label} ({part})", result.manual or ""
                )
                plan.manual.append(note)
                plan.items.append(
                    manual_item(
                        note, text, key=f"mb:{branch.id}:{part}", depends_on=(PLATFORM_KEY,)
                    )
                )
            elif result.changed:
                changes[part] = result.sql
        if changes:
            plan.items.append(
                ProposedItem(
                    key=f"tb:{branch.id}",
                    object_type="mapping_branch",
                    operation="update",
                    object_id=branch.id,
                    label=label,
                    payload=changes,
                    depends_on=(PLATFORM_KEY,),
                )
            )


# -- handlers -------------------------------------------------------------------------------


class DataWarehouseHandler:
    """The ``data_warehouse`` item: the target platform. Owner-only. Changing it is structural,
    so the approvals watcher returns an approved Core and Mart to draft in the same accept."""

    object_type = DATA_WAREHOUSE

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("Change Sets only change the Data Warehouse's target platform.")
        return Action.CHANGE_DW_PLATFORM

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if (
            set(payload) != {"target_platform"}
            or payload["target_platform"] not in TARGET_PLATFORMS
        ):
            raise _invalid(
                "A data_warehouse item changes only its target_platform, "
                f"one of {', '.join(TARGET_PLATFORMS)}."
            )
        if self._record(db, workspace_id, object_id) is None:
            raise _invalid("The Data Warehouse does not exist.")

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        return None if record is None else {f: getattr(record, f) for f in fields}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The Data Warehouse no longer exists.")
        old = record.target_platform
        record.target_platform = payload["target_platform"]
        record.version += 1
        record.updated_at = at
        db.flush()
        return AppliedChange(
            DATA_WAREHOUSE,
            record.id,
            {"target_platform": old},
            {"target_platform": record.target_platform},
            "Data Warehouse",
        )

    @staticmethod
    def _record(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> DataWarehouseRecord | None:
        if object_id is None:
            return None
        query = sa.select(DataWarehouseRecord).where(
            DataWarehouseRecord.id == object_id, DataWarehouseRecord.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        return db.scalars(query).first()


class TranspiledMappingHandler(ColumnMappingHandler):
    """The ``column_mapping_sql`` item: a Core or Mart mapping's SQL in the new dialect. It is
    re-parsed and its lineage re-derived on apply, as a save does; text that no longer
    resolves is kept with a validation error."""

    object_type = "column_mapping_sql"
    FIELDS = ("sql_expression",)

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if set(payload) != {"sql_expression"} or not isinstance(payload["sql_expression"], str):
            raise _invalid("A column_mapping_sql item changes only its sql_expression.")
        if self._mapping(db, workspace_id, object_id) is None:
            raise _invalid("The column mapping does not exist.")


class ManualReviewHandler:
    """The ``manual_review`` item: SQL a platform change could not translate. Accepting it
    acknowledges that a person must rewrite the text; the SQL is not touched, and the flag is
    kept in the audit trail."""

    object_type = MANUAL_REVIEW

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("A manual_review item flags existing SQL.")
        return Action.EDIT_DW_SCHEMA

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if set(payload) != {"target_type", "reason", "sql"}:
            raise _invalid("A manual_review item names a target_type, a reason and the sql.")

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        return {}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        return AppliedChange(
            payload["target_type"],
            object_id or uuid.uuid4(),
            {},
            {"manual_translation": payload["reason"]},
            "needs manual translation",
        )


__all__ = [
    "DATA_WAREHOUSE",
    "MANUAL_REVIEW",
    "PLATFORM_KEY",
    "DataWarehouseHandler",
    "ManualNote",
    "ManualReviewHandler",
    "PlatformPlan",
    "TranspiledMappingHandler",
    "manual_item",
    "plan_platform_change",
    "plan_system_code_change",
]
