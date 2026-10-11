"""The Change Set handlers propagation items use (spec §6.10); a ``dw_column`` rename or
retype goes through ``generation.DwColumnHandler``.

- ``ColumnMappingHandler`` (``column_mapping``): rewrites a column mapping's SQL or lookup
  inputs to follow a rename, or makes it ``unmapped`` when an input was removed. A mapping
  sheet import also creates a mapping and sets its type (never ``lookup``, ``system`` or
  ``hashed``), rule and SQL; those values are checked as a save checks them.
- ``MappingBranchHandler`` (``mapping_branch``): rewrites a branch's driving input, joins,
  filters, GROUP BY or HAVING to follow a rename.

A mapping or branch is re-derived on apply exactly as a save derives it (parse, resolve the
inputs in the Layer below, replace its lineage edges). Text that still names something
missing is kept with a ``broken_reference`` error and no edges, so one leftover reference
never aborts the whole accept.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import AppliedChange
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .lineage_sql import BRANCH_PARTS
from .mapping_service import MappingService
from .model_service import MODEL_LAYERS
from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
    MappingBranchRecord,
    TableMappingRecord,
)


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


def _helpers(db: Session) -> MappingService:
    """A ``MappingService`` for its SQL helpers (parse, resolve, derive edges), which work in
    the session they are given and authorize nothing."""
    return MappingService(db.get_bind(), workspaces=None, clock=None)  # type: ignore[arg-type]


def _warehouse(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord | None:
    return db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()


class _UpdateOnly:
    object_type: str
    operations: tuple[str, ...] = ("update",)

    def required_action(self, operation: str) -> Action:
        if operation not in self.operations:
            raise ValueError(f"Change Sets cannot {operation} {self.object_type} objects.")
        return Action.EDIT_DW_SCHEMA


def _broken(message: str) -> dict[str, Any]:
    return {"unparsed": False, "errors": [{"code": "broken_reference", "message": message}]}


IMPORTABLE_TYPES = ("direct", "derived", "constant", "unmapped", "not_in_branch")
"""The mapping types an import may set; the others are made by the tool itself."""


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise _invalid(f"`{what}` is an id.") from None


class ColumnMappingHandler(_UpdateOnly):
    object_type = "column_mapping"
    operations = ("update", "create")
    FIELDS = ("mapping_type", "rule_text", "sql_expression", "lookup")
    CREATE_FIELDS = ("dw_column_id", "branch_id", "mapping_type", "rule_text", "sql_expression")

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if operation == "create":
            if any(f not in self.CREATE_FIELDS for f in payload) or "mapping_type" not in payload:
                raise _invalid(f"A new column_mapping has {', '.join(self.CREATE_FIELDS)}.")
            column_id = _uuid(payload.get("dw_column_id"), "dw_column_id")
            branch_id = payload.get("branch_id")
            found = self._column(db, workspace_id, column_id)
            if found is None:
                raise _invalid("The column does not exist.")
            if branch_id is not None and not self._in_table(
                db, _uuid(branch_id, "branch_id"), found[1]
            ):
                raise _invalid("The branch does not belong to the column's table.")
            self.check(db, workspace_id, column_id, branch_id is not None, payload)
            return
        if not payload or any(f not in self.FIELDS for f in payload):
            raise _invalid(f"A column_mapping item changes some of {', '.join(self.FIELDS)}.")
        found_mapping = self._mapping(db, workspace_id, object_id)
        if found_mapping is None:
            raise _invalid("The column mapping does not exist.")
        if payload.get("mapping_type", "unmapped") != "unmapped":
            self.check(
                db,
                workspace_id,
                found_mapping[1].id,
                found_mapping[0].branch_id is not None,
                payload,
            )

    def check(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        column_id: uuid.UUID,
        in_branch: bool,
        fields: Mapping[str, Any],
    ) -> None:
        """Refuse a type, rule and SQL a save would refuse, and SQL naming a column the Layer
        below does not have (422 ``invalid_change_set``)."""
        found = self._column(db, workspace_id, column_id)
        warehouse = _warehouse(db, workspace_id)
        if found is None or warehouse is None:
            raise _invalid("The column does not exist.")
        column, table = found
        mapping_type = fields.get("mapping_type")
        if mapping_type not in IMPORTABLE_TYPES:
            raise _invalid(f"The type must be one of {', '.join(IMPORTABLE_TYPES)}.")
        helpers = _helpers(db)
        values = {
            "mapping_type": mapping_type,
            "rule_text": fields.get("rule_text") or "",
            "sql_expression": fields.get("sql_expression") or "",
        }
        try:
            _, _, sql = helpers._fields(values, in_branch=in_branch, column=column)
            parsed, validation = helpers._parse(warehouse, mapping_type, sql)
            if validation["unparsed"]:
                raise ApiError(422, "invalid_mapping", validation["errors"][0]["message"])
            if parsed:
                helpers._resolve(db, warehouse, table, parsed)
        except ApiError as exc:
            raise _invalid(exc.message) from None

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        found = self._mapping(db, workspace_id, object_id, lock=True)
        return None if found is None else {f: getattr(found[0], f) for f in fields}

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
        warehouse = _warehouse(db, workspace_id)
        if operation == "create":
            found = self._create(db, workspace_id, payload, at)
        else:
            found = self._mapping(db, workspace_id, object_id, lock=True)
        if found is None or warehouse is None:  # pragma: no cover - checked under the lock
            raise ApiError(409, "version_conflict", "The column mapping no longer exists.")
        record, column, table = found
        old = {f: getattr(record, f) for f in self.FIELDS}
        helpers = _helpers(db)
        mapping_type = payload.get("mapping_type", record.mapping_type)
        sql = payload.get("sql_expression", record.sql_expression) or ""
        lookup = payload.get("lookup", record.lookup)
        parsed = dimension = None
        validation: dict[str, Any] = {"unparsed": False, "errors": []}
        inputs: dict[Any, Any] = {}
        if mapping_type in ("unmapped", "not_in_branch"):
            sql, lookup = "", None
        else:
            try:
                if mapping_type == "lookup":
                    lookup, parsed, dimension = helpers._lookup(
                        db, warehouse, table, column, lookup
                    )
                else:
                    parsed, validation = helpers._parse(warehouse, mapping_type, sql)
                inputs = helpers._resolve(db, warehouse, table, parsed) if parsed else {}
            except ApiError as exc:
                parsed, dimension, inputs = None, None, {}
                validation = _broken(exc.message)
        record.mapping_type = mapping_type
        record.rule_text = payload.get("rule_text", record.rule_text) or ""
        record.sql_expression = sql
        record.lookup = lookup
        record.validation = validation
        record.updated_by = None
        record.updated_at = at
        record.version += 1
        db.flush()
        helpers._replace_edges(db, record, table, parsed, inputs, dimension)
        new = {f: getattr(record, f) for f in self.FIELDS}
        changed = [f for f in self.FIELDS if old[f] != new[f]]
        return AppliedChange(
            "column_mapping",
            record.id,
            None if operation == "create" else {f: old[f] for f in changed},
            {f: new[f] for f in changed},
            f"{table.name}.{column.name}",
        )

    def _create(
        self, db: Session, workspace_id: uuid.UUID, payload: Mapping[str, Any], at: datetime
    ) -> tuple[ColumnMappingRecord, DwColumnRecord, DwTableRecord] | None:
        """The mapping to fill: the column's existing one if someone made it meanwhile,
        else a new empty one."""
        column_id = _uuid(payload["dw_column_id"], "dw_column_id")
        found = self._column(db, workspace_id, column_id, lock=True)
        if found is None:
            return None
        column, table = found
        raw_branch = payload.get("branch_id")
        branch_id = _uuid(raw_branch, "branch_id") if raw_branch else None
        same_branch = (
            ColumnMappingRecord.branch_id.is_(None)
            if branch_id is None
            else ColumnMappingRecord.branch_id == branch_id
        )
        record = db.scalars(
            sa.select(ColumnMappingRecord)
            .where(ColumnMappingRecord.dw_column_id == column.id, same_branch)
            .with_for_update()
        ).first()
        if record is None:
            table_mapping = _helpers(db)._table_mapping(db, table, at)
            record = ColumnMappingRecord(
                id=uuid.uuid4(),
                table_mapping_id=table_mapping.id,
                branch_id=branch_id,
                dw_column_id=column.id,
                mapping_type="unmapped",
                rule_text="",
                sql_expression="",
                lookup=None,
                validation={"unparsed": False, "errors": []},
                updated_at=at,
                version=0,
            )
            db.add(record)
            db.flush()
        return record, column, table

    @staticmethod
    def _column(
        db: Session, workspace_id: uuid.UUID, column_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[DwColumnRecord, DwTableRecord] | None:
        query = (
            sa.select(DwColumnRecord, DwTableRecord)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DataWarehouseRecord.workspace_id == workspace_id,
                DwTableRecord.layer.in_(MODEL_LAYERS),
                DwColumnRecord.id == column_id,
            )
        )
        if lock:
            query = query.with_for_update(of=DwColumnRecord)
        row = db.execute(query).first()
        return None if row is None else (row[0], row[1])

    @staticmethod
    def _in_table(db: Session, branch_id: uuid.UUID, table: DwTableRecord) -> bool:
        return (
            db.scalars(
                sa.select(MappingBranchRecord.id)
                .join(
                    TableMappingRecord,
                    TableMappingRecord.id == MappingBranchRecord.table_mapping_id,
                )
                .where(
                    MappingBranchRecord.id == branch_id, TableMappingRecord.dw_table_id == table.id
                )
            ).first()
            is not None
        )

    @staticmethod
    def _mapping(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> tuple[ColumnMappingRecord, DwColumnRecord, DwTableRecord] | None:
        if object_id is None:
            return None
        query = (
            sa.select(ColumnMappingRecord, DwColumnRecord, DwTableRecord)
            .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DataWarehouseRecord.workspace_id == workspace_id,
                DwTableRecord.layer.in_(MODEL_LAYERS),
                ColumnMappingRecord.id == object_id,
            )
        )
        if lock:
            query = query.with_for_update(of=ColumnMappingRecord)
        row = db.execute(query).first()
        return None if row is None else (row[0], row[1], row[2])


class MappingBranchHandler(_UpdateOnly):
    object_type = "mapping_branch"

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if not payload or any(f not in BRANCH_PARTS for f in payload):
            raise _invalid(f"A mapping_branch item changes some of {', '.join(BRANCH_PARTS)}.")
        if self._branch(db, workspace_id, object_id) is None:
            raise _invalid("The mapping branch does not exist.")

    def check(
        self, db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID, fields: Mapping[str, Any]
    ) -> None:
        """Refuse branch text a save would refuse: SQL that does not parse, or naming a table
        the Layer below does not have (422 ``invalid_change_set``). ``fields`` holds the
        branch's ``name`` and its ``BRANCH_PARTS``."""
        warehouse = _warehouse(db, workspace_id)
        table = db.get(DwTableRecord, table_id)
        if warehouse is None or table is None:
            raise _invalid("The table does not exist.")
        try:
            _helpers(db)._branch_values(db, warehouse, table, fields)
        except ApiError as exc:
            raise _invalid(exc.message) from None

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        found = self._branch(db, workspace_id, object_id, lock=True)
        return None if found is None else {f: getattr(found[0], f) for f in fields}

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
        found = self._branch(db, workspace_id, object_id, lock=True)
        warehouse = _warehouse(db, workspace_id)
        if found is None or warehouse is None:  # pragma: no cover - checked under the lock
            raise ApiError(409, "version_conflict", "The mapping branch no longer exists.")
        branch, table = found
        old = {f: getattr(branch, f) for f in payload}
        for part, value in payload.items():
            setattr(branch, part, value)
        helpers = _helpers(db)
        fields = {"name": branch.name, **{p: getattr(branch, p) for p in BRANCH_PARTS}}
        try:
            _, parts = helpers._branch_values(db, warehouse, table, fields)
            helpers._replace_branch_edges(db, warehouse, table, branch, parts)
        except ApiError:
            # Still naming something missing: keep the text; validation reports it.
            db.execute(sa.delete(LineageEdgeRecord).where(LineageEdgeRecord.branch_id == branch.id))
        branch.version += 1
        branch.updated_at = at
        db.flush()
        return AppliedChange(
            "mapping_branch",
            branch.id,
            old,
            {f: getattr(branch, f) for f in payload},
            f"{table.name}: {branch.name}",
        )

    @staticmethod
    def _branch(
        db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
    ) -> tuple[MappingBranchRecord, DwTableRecord] | None:
        if object_id is None:
            return None
        query = (
            sa.select(MappingBranchRecord, DwTableRecord)
            .join(TableMappingRecord, TableMappingRecord.id == MappingBranchRecord.table_mapping_id)
            .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DataWarehouseRecord.workspace_id == workspace_id,
                MappingBranchRecord.id == object_id,
            )
        )
        if lock:
            query = query.with_for_update(of=MappingBranchRecord)
        row = db.execute(query).first()
        return None if row is None else (row[0], row[1])
