"""Staging sync and "drop removed" as Change Set items (spec §6.7, stories 86, 86a).

``plan_sync`` compares one Source System's latest Source Schema with its Staging Tables and
proposes, for staging only: new Staging Tables (a create with its columns nested), new
columns of existing ones, changed column types and nullability, and ``source_removed``
flags for what the source dropped (kept, never deleted). A Source Table whose Staging Table a
user deleted has a Tombstone and is never proposed again; a field the user overrode
(``edited_fields``) is proposed as a conflict, not overwritten. A rename keeps the Source
Object's identity, so it shows up as neither a create nor a removal.

``plan_drop_removed`` proposes the owner-only deletes of ``source_removed`` staging objects
that no mapping reads.

``StagingTableHandler`` and ``StagingColumnHandler`` are the ``changesets`` engine's handlers
for ``staging_table`` and ``staging_column`` items. A create carries everything it needs in
its payload (names, types, mappings), so accepting 2,000 of them reads nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import AppliedChange, ProposedItem
from dawam.modules.sources import StagingSystem, StagingTable
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .platforms import TargetPlatform
from .service import NamingRules
from .staging_naming import NameInput, column_names, stable_hash
from .staging_rows import _quote, _Rows, build_staging, write_rows
from .staging_types import translate_type
from .tables import (
    NAME_MAX_LENGTH,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
    TableMappingRecord,
    TombstoneRecord,
)

TABLE = "staging_table"
COLUMN = "staging_column"

_TYPE_FLAGS = ("lossy_type", "fallback_type")
"""Review flags that come from the type translation (the rest come from the name)."""


def _dicts(flags: Sequence[Any]) -> list[dict[str, str]]:
    return [{"code": f.code, "message": f.message} for f in flags]


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


# -- planning -------------------------------------------------------------------------------


def _spec(rows: _Rows) -> list[dict[str, Any]]:
    """The create payload of each table of ``rows``: what the handler writes on accept."""
    mappings = {m["dw_column_id"]: m for m in rows.column_mappings}
    specs = []
    for table in rows.tables:
        columns = sorted(
            (c for c in rows.columns if c["table_id"] == table["id"]), key=lambda c: c["ordinal"]
        )
        specs.append(
            {
                "source_table_id": str(table["source_table_id"]),
                "name": table["name"],
                "review_flags": table["review_flags"],
                "columns": [
                    {
                        "name": c["name"],
                        "data_type": c["data_type"],
                        "is_nullable": c["is_nullable"],
                        "role": c["role"],
                        "is_system": c["is_system"],
                        "source_column_id": (
                            str(c["source_column_id"]) if c["source_column_id"] else None
                        ),
                        "review_flags": c["review_flags"],
                        "mapping_type": mappings[c["id"]]["mapping_type"],
                        "sql_expression": mappings[c["id"]]["sql_expression"],
                    }
                    for c in columns
                ],
            }
        )
    return specs


def plan_sync(
    db: Session,
    *,
    warehouse: DataWarehouseRecord,
    system: StagingSystem,
    taken: set[str],
    now: datetime,
) -> list[ProposedItem]:
    """The sync items for ``system`` (read with its removed objects, placeholders assigned)."""
    platform: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
    rules = NamingRules(**warehouse.naming_rules)
    audit_names = (rules.load_ts_column, rules.source_system_column)
    source_ids = [t.id for t in system.tables]
    staged = {
        t.source_table_id: t
        for t in db.scalars(
            sa.select(DwTableRecord).where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer == "staging",
                DwTableRecord.source_table_id.in_(source_ids),
            )
        )
    }
    tombstoned = set(
        db.scalars(
            sa.select(TombstoneRecord.src_object_id).where(
                TombstoneRecord.data_warehouse_id == warehouse.id,
                TombstoneRecord.object_type == "staging_table",
                TombstoneRecord.src_object_id.in_(source_ids),
            )
        )
    )
    columns_of: dict[uuid.UUID, list[DwColumnRecord]] = {}
    for column in db.scalars(
        sa.select(DwColumnRecord).where(
            DwColumnRecord.table_id.in_([t.id for t in staged.values()])
        )
    ):
        columns_of.setdefault(column.table_id, []).append(column)

    items: list[ProposedItem] = []
    new_tables: list[StagingTable] = []
    for table in system.tables:
        dw = staged.get(table.id)
        if dw is None:
            if table.status == "present" and table.id not in tombstoned:
                new_tables.append(table)
            continue
        by_source = {c.source_column_id: c for c in columns_of.get(dw.id, []) if c.source_column_id}
        if table.status == "source_removed" and dw.status == "present":
            items.append(_status(TABLE, "t", dw.id, dw.name, "source_removed"))
        elif table.status == "present" and dw.status == "source_removed":
            items.append(_status(TABLE, "t", dw.id, dw.name, "present"))
        if table.status != "present":
            continue
        taken_columns = {c.name for c in columns_of.get(dw.id, [])}
        fresh = [c for c in table.columns if c.id not in by_source and c.status == "present"]
        named = column_names(
            platform,
            columns=[NameInput(str(c.id), c.name, c.placeholder_no) for c in fresh],
            taken=taken_columns,
            max_length=NAME_MAX_LENGTH,
        )
        source_prefix = ".".join(_quote(p) for p in (table.db_schema, table.name))
        for column in fresh:
            translated = translate_type(platform, column.data_type, engine=system.engine)
            name = named[str(column.id)]
            items.append(
                ProposedItem(
                    key=f"nc:{column.id}",
                    object_type=COLUMN,
                    operation="create",
                    label=f"{dw.name}.{name.name}",
                    payload={
                        "table_id": str(dw.id),
                        "source_column_id": str(column.id),
                        "name": name.name,
                        "data_type": translated.data_type,
                        "is_nullable": column.is_nullable,
                        "review_flags": _dicts([*name.flags, *translated.flags]),
                        "sql_expression": f"{source_prefix}.{_quote(column.name)}",
                    },
                )
            )
        for column in table.columns:
            dwc = by_source.get(column.id)
            if dwc is None:
                continue
            if column.status == "source_removed" and dwc.status == "present":
                items.append(
                    _status(COLUMN, "c", dwc.id, f"{dw.name}.{dwc.name}", "source_removed")
                )
            elif column.status == "present" and dwc.status == "source_removed":
                items.append(_status(COLUMN, "c", dwc.id, f"{dw.name}.{dwc.name}", "present"))
            if column.status != "present":
                continue
            translated = translate_type(platform, column.data_type, engine=system.engine)
            changes: dict[str, Any] = {}
            if translated.data_type != dwc.data_type:
                changes["data_type"] = translated.data_type
            if column.is_nullable != dwc.is_nullable:
                changes["is_nullable"] = column.is_nullable
            if not changes:
                continue
            payload = dict(changes)
            if "data_type" in changes:
                kept = [f for f in dwc.review_flags if f.get("code") not in _TYPE_FLAGS]
                payload["review_flags"] = [*kept, *_dicts(translated.flags)]
            items.append(
                ProposedItem(
                    key=f"uc:{dwc.id}",
                    object_type=COLUMN,
                    operation="update",
                    object_id=dwc.id,
                    label=f"{dw.name}.{dwc.name}",
                    payload=payload,
                    is_conflict=any(f in dwc.edited_fields for f in changes),
                )
            )
    if new_tables:
        rows, _ = build_staging(
            None,
            warehouse.id,
            platform,
            audit_names,
            [StagingSystem(system.id, system.code, system.engine, new_tables)],
            taken,
            now,
        )
        for spec in _spec(rows):
            items.append(
                ProposedItem(
                    key=f"nt:{spec['source_table_id']}",
                    object_type=TABLE,
                    operation="create",
                    label=spec["name"],
                    payload=spec,
                )
            )
    return items


def _status(
    object_type: str, prefix: str, object_id: uuid.UUID, label: str, status: str
) -> ProposedItem:
    return ProposedItem(
        key=f"s{prefix}:{object_id}",
        object_type=object_type,
        operation="update",
        object_id=object_id,
        label=label,
        payload={"status": status},
    )


def plan_drop_removed(db: Session, warehouse: DataWarehouseRecord) -> list[ProposedItem]:
    """Owner-only deletes of ``source_removed`` staging objects nothing downstream reads."""
    items: list[ProposedItem] = []
    tables = list(
        db.scalars(
            sa.select(DwTableRecord)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id, DwTableRecord.layer == "staging"
            )
            .order_by(DwTableRecord.name)
        )
    )
    columns: dict[uuid.UUID, list[DwColumnRecord]] = {}
    for column in db.scalars(
        sa.select(DwColumnRecord)
        .where(DwColumnRecord.table_id.in_([t.id for t in tables]))
        .order_by(DwColumnRecord.ordinal)
    ):
        columns.setdefault(column.table_id, []).append(column)
    used = set(
        db.scalars(
            sa.select(LineageEdgeRecord.from_id).where(
                LineageEdgeRecord.from_id.in_([c.id for cs in columns.values() for c in cs])
            )
        )
    )
    for table in tables:
        if table.status == "source_removed":
            if not any(c.id in used for c in columns.get(table.id, [])):
                items.append(
                    ProposedItem(
                        key=f"dt:{table.id}",
                        object_type=TABLE,
                        operation="delete",
                        object_id=table.id,
                        label=table.name,
                        payload={},
                        base_values={"status": "source_removed", "downstream_uses": 0},
                    )
                )
            continue
        for column in columns.get(table.id, []):
            if column.status == "source_removed" and column.id not in used:
                items.append(
                    ProposedItem(
                        key=f"dc:{column.id}",
                        object_type=COLUMN,
                        operation="delete",
                        object_id=column.id,
                        label=f"{table.name}.{column.name}",
                        payload={},
                        base_values={"status": "source_removed", "downstream_uses": 0},
                    )
                )
    return items


# -- handlers -------------------------------------------------------------------------------


def _workspace_tables(workspace_id: uuid.UUID) -> sa.Select[tuple[DwTableRecord]]:
    return (
        sa.select(DwTableRecord)
        .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
        .where(DataWarehouseRecord.workspace_id == workspace_id, DwTableRecord.layer == "staging")
    )


def _uses(db: Session, column_ids: Sequence[uuid.UUID]) -> int:
    """Lineage edges that read one of the columns: what downstream use means."""
    if not column_ids:
        return 0
    return (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(LineageEdgeRecord)
            .where(LineageEdgeRecord.from_id.in_(column_ids))
        )
        or 0
    )


def _in_scope(db: Session, system_id: Any, source_table_id: uuid.UUID | None) -> bool:
    if system_id is None:
        return True
    if source_table_id is None:
        return False
    owner = db.scalar(
        sa.text(
            "select d.source_system_id from src_tables t"
            " join src_db_schemas d on d.id = t.db_schema_id where t.id = :t"
        ),
        {"t": source_table_id},
    )
    return str(owner) == str(system_id)


class _Handler:
    object_type: str

    def required_action(self, operation: str) -> Action:
        if operation == "delete":
            return Action.RETIRE_STAGING
        return Action.EDIT_DW_SCHEMA


class StagingTableHandler(_Handler):
    object_type = TABLE

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if operation == "create":
            if not payload.get("source_table_id") or not payload.get("columns"):
                raise _invalid("A staging table create names its source table and columns.")
            return
        if operation == "update" and set(payload) != {"status"}:
            raise _invalid("A staging table item changes only its status.")
        if self._table(db, workspace_id, object_id) is None:
            raise _invalid("The Staging Table does not exist.")

    def in_scope(
        self, db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID, scope: Mapping[str, Any]
    ) -> bool:
        table = self._table(db, workspace_id, object_id)
        return table is not None and _in_scope(
            db, scope.get("source_system_id"), table.source_table_id
        )

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        table = self._table(db, workspace_id, object_id, lock=True)
        if table is None:
            return None
        values: dict[str, Any] = {}
        for field in fields:
            if field == "status":
                values[field] = table.status
            elif field == "downstream_uses":
                ids = list(
                    db.scalars(
                        sa.select(DwColumnRecord.id).where(DwColumnRecord.table_id == table.id)
                    )
                )
                values[field] = _uses(db, ids)
        return values

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
        if operation == "create":
            return self._create(db, workspace_id, payload, at)
        table = self._table(db, workspace_id, object_id, lock=True)
        if table is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The Staging Table no longer exists.")
        if operation == "update":
            old = table.status
            table.status = payload["status"]
            table.version += 1
            table.updated_at = at
            db.flush()
            return AppliedChange(
                TABLE, table.id, {"status": old}, {"status": table.status}, table.name
            )
        warehouse_id = table.data_warehouse_id
        if table.source_table_id is not None:
            db.add(
                TombstoneRecord(
                    data_warehouse_id=warehouse_id,
                    object_type="staging_table",
                    src_object_id=table.source_table_id,
                    deleted_at=at,
                )
            )
        name, old_values = table.name, {"name": table.name, "status": table.status}
        db.delete(table)
        db.flush()
        return AppliedChange(TABLE, object_id or uuid.uuid4(), old_values, None, name)

    def _create(
        self, db: Session, workspace_id: uuid.UUID, payload: Mapping[str, Any], at: datetime
    ) -> AppliedChange:
        warehouse = db.scalars(
            sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
        ).one()
        source_id = uuid.UUID(payload["source_table_id"])
        if db.scalar(
            sa.select(DwTableRecord.id).where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer == "staging",
                DwTableRecord.source_table_id == source_id,
            )
        ):
            raise ApiError(409, "already_staged", f"{payload['name']} was staged in the meantime.")
        if db.scalar(
            sa.select(TombstoneRecord.id).where(
                TombstoneRecord.data_warehouse_id == warehouse.id,
                TombstoneRecord.src_object_id == source_id,
            )
        ):
            raise ApiError(409, "tombstoned", f"{payload['name']} was deleted in the meantime.")
        name = _free_table_name(db, warehouse.id, payload["name"], source_id)
        rows = _Rows()
        table_id = uuid.uuid4()
        mapping_id = uuid.uuid4()
        rows.tables.append(
            {
                "id": table_id,
                "data_warehouse_id": warehouse.id,
                "layer": "staging",
                "name": name,
                "kind": "staging",
                "is_aggregate": False,
                "is_conformed": False,
                "description": "",
                "source_table_id": source_id,
                "review_flags": payload["review_flags"],
                "created_by": None,
                "created_at": at,
                "updated_at": at,
                "version": 1,
            }
        )
        rows.table_mappings.append(
            {
                "id": mapping_id,
                "dw_table_id": table_id,
                "match_keys": [],
                "notes": "",
                "created_at": at,
                "updated_at": at,
                "version": 1,
            }
        )
        for ordinal, spec in enumerate(payload["columns"], start=1):
            _column_rows(rows, table_id, mapping_id, ordinal, spec, at)
        write_rows(db, rows)
        return AppliedChange(
            TABLE,
            table_id,
            None,
            {"name": name, "columns": len(payload["columns"])},
            name,
        )

    @staticmethod
    def _table(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> DwTableRecord | None:
        if object_id is None:
            return None
        query = _workspace_tables(workspace_id).where(DwTableRecord.id == object_id)
        if lock:
            query = query.with_for_update(of=DwTableRecord)
        return db.scalars(query).first()


class StagingColumnHandler(_Handler):
    object_type = COLUMN
    _UPDATABLE = ("data_type", "is_nullable", "review_flags", "status")

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if operation == "create":
            table = StagingTableHandler._table(db, workspace_id, _uuid(payload.get("table_id")))
            if table is None or not payload.get("source_column_id") or not payload.get("name"):
                raise _invalid("A staging column create names its Staging Table and source column.")
            return
        if operation == "update" and (
            not payload or any(f not in self._UPDATABLE for f in payload)
        ):
            raise _invalid(f"A staging column item changes some of {', '.join(self._UPDATABLE)}.")
        if self._column(db, workspace_id, object_id) is None:
            raise _invalid("The staging column does not exist.")

    def in_scope(
        self, db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID, scope: Mapping[str, Any]
    ) -> bool:
        found = self._column(db, workspace_id, object_id)
        return found is not None and _in_scope(
            db, scope.get("source_system_id"), found[1].source_table_id
        )

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        found = self._column(db, workspace_id, object_id, lock=True)
        if found is None:
            return None
        column = found[0]
        values: dict[str, Any] = {}
        for field in fields:
            if field == "downstream_uses":
                values[field] = _uses(db, [column.id])
            else:
                values[field] = getattr(column, field)
        return values

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
        if operation == "create":
            return self._create(db, workspace_id, payload, at)
        found = self._column(db, workspace_id, object_id, lock=True)
        if found is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The staging column no longer exists.")
        column, table = found
        if operation == "delete":
            old = {"name": column.name, "status": column.status}
            db.delete(column)
            db.flush()
            siblings = db.scalars(
                sa.select(DwColumnRecord)
                .where(DwColumnRecord.table_id == table.id)
                .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
            )
            for ordinal, remaining in enumerate(siblings, start=1):
                remaining.ordinal = ordinal
            return AppliedChange(COLUMN, object_id or column.id, old, None, column.name)
        old = {f: getattr(column, f) for f in payload}
        for field, value in payload.items():
            setattr(column, field, value)
        # Taking the source's value ends the user's override of it.
        column.edited_fields = [f for f in column.edited_fields if f not in payload]
        column.version += 1
        column.updated_at = at
        db.flush()
        return AppliedChange(COLUMN, column.id, old, dict(payload), f"{table.name}.{column.name}")

    def _create(
        self, db: Session, workspace_id: uuid.UUID, payload: Mapping[str, Any], at: datetime
    ) -> AppliedChange:
        table = StagingTableHandler._table(db, workspace_id, _uuid(payload["table_id"]), lock=True)
        if table is None:
            raise ApiError(409, "version_conflict", "The Staging Table no longer exists.")
        source_column_id = uuid.UUID(payload["source_column_id"])
        if db.scalar(
            sa.select(DwColumnRecord.id).where(
                DwColumnRecord.table_id == table.id,
                DwColumnRecord.source_column_id == source_column_id,
            )
        ):
            raise ApiError(409, "already_staged", f"{payload['name']} was staged in the meantime.")
        taken = {
            c.lower()
            for c in db.scalars(
                sa.select(DwColumnRecord.name).where(DwColumnRecord.table_id == table.id)
            )
        }
        name = payload["name"]
        if name.lower() in taken:
            suffix = "_" + stable_hash(str(source_column_id))
            name = name[: NAME_MAX_LENGTH - len(suffix)] + suffix
        # New source columns go before the audit columns, which stay last.
        audits = list(
            db.scalars(
                sa.select(DwColumnRecord)
                .where(DwColumnRecord.table_id == table.id, DwColumnRecord.role == "audit")
                .order_by(DwColumnRecord.ordinal)
            )
        )
        top = db.scalar(
            sa.select(sa.func.coalesce(sa.func.max(DwColumnRecord.ordinal), 0)).where(
                DwColumnRecord.table_id == table.id, DwColumnRecord.role != "audit"
            )
        )
        ordinal = (top or 0) + 1
        for shift, audit in enumerate(audits, start=1):
            audit.ordinal = ordinal + shift
        db.flush()
        mapping_id = db.scalar(
            sa.select(TableMappingRecord.id).where(TableMappingRecord.dw_table_id == table.id)
        )
        rows = _Rows()
        _column_rows(
            rows,
            table.id,
            mapping_id,
            ordinal,
            {
                "name": name,
                "data_type": payload["data_type"],
                "is_nullable": payload["is_nullable"],
                "role": "attribute",
                "is_system": False,
                "source_column_id": payload["source_column_id"],
                "review_flags": payload.get("review_flags", []),
                "mapping_type": "direct",
                "sql_expression": payload["sql_expression"],
            },
            at,
        )
        write_rows(db, rows)
        table.version += 1
        table.updated_at = at
        return AppliedChange(
            COLUMN,
            rows.columns[0]["id"],
            None,
            {"name": name, "data_type": payload["data_type"]},
            f"{table.name}.{name}",
        )

    @staticmethod
    def _column(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> tuple[DwColumnRecord, DwTableRecord] | None:
        if object_id is None:
            return None
        query = (
            sa.select(DwColumnRecord, DwTableRecord)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DataWarehouseRecord.workspace_id == workspace_id,
                DwTableRecord.layer == "staging",
                DwColumnRecord.id == object_id,
            )
        )
        if lock:
            query = query.with_for_update(of=DwColumnRecord)
        row = db.execute(query).first()
        return None if row is None else (row[0], row[1])


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _free_table_name(db: Session, warehouse_id: uuid.UUID, name: str, source_id: uuid.UUID) -> str:
    """``name``, or with the stable hash suffix when another staging table took it since the
    sync was proposed."""
    taken = db.scalar(
        sa.select(DwTableRecord.id).where(
            DwTableRecord.data_warehouse_id == warehouse_id,
            DwTableRecord.layer == "staging",
            sa.func.lower(DwTableRecord.name) == name.lower(),
        )
    )
    if taken is None:
        return name
    suffix = "_" + stable_hash(str(source_id))
    return name[: NAME_MAX_LENGTH - len(suffix)] + suffix


def _column_rows(
    rows: _Rows,
    table_id: uuid.UUID,
    mapping_id: uuid.UUID,
    ordinal: int,
    spec: Mapping[str, Any],
    at: datetime,
) -> None:
    column_id = uuid.uuid4()
    source_column_id = uuid.UUID(spec["source_column_id"]) if spec["source_column_id"] else None
    rows.columns.append(
        {
            "id": column_id,
            "table_id": table_id,
            "name": spec["name"],
            "ordinal": ordinal,
            "data_type": spec["data_type"],
            "is_nullable": spec["is_nullable"],
            "role": spec["role"],
            "description": "",
            "is_system": spec["is_system"],
            "source_column_id": source_column_id,
            "review_flags": spec["review_flags"],
            "created_at": at,
            "updated_at": at,
            "version": 1,
        }
    )
    column_mapping_id = uuid.uuid4()
    rows.column_mappings.append(
        {
            "id": column_mapping_id,
            "table_mapping_id": mapping_id,
            "dw_column_id": column_id,
            "mapping_type": spec["mapping_type"],
            "rule_text": "",
            "sql_expression": spec["sql_expression"],
            "validation": {"unparsed": False, "errors": []},
            "updated_by": None,
            "updated_at": at,
            "version": 1,
        }
    )
    if source_column_id is not None:
        rows.edges.append(
            {
                "id": uuid.uuid4(),
                "kind": "value",
                "from_type": "src_column",
                "from_id": source_column_id,
                "to_type": "dw_column",
                "to_id": column_id,
                "mapping_id": column_mapping_id,
            }
        )


__all__ = [
    "COLUMN",
    "TABLE",
    "StagingColumnHandler",
    "StagingTableHandler",
    "plan_drop_removed",
    "plan_sync",
]
