"""The Change Set handlers propagation items use (spec §6.10); a ``dw_column`` rename or
retype goes through ``generation.DwColumnHandler``.

- ``ColumnMappingHandler`` (``column_mapping``): rewrites a column mapping's SQL or lookup
  inputs to follow a rename, or makes it ``unmapped`` when an input was removed.
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

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError(f"Change Sets only update {self.object_type} objects.")
        return Action.EDIT_DW_SCHEMA


def _broken(message: str) -> dict[str, Any]:
    return {"unparsed": False, "errors": [{"code": "broken_reference", "message": message}]}


class ColumnMappingHandler(_UpdateOnly):
    object_type = "column_mapping"
    FIELDS = ("mapping_type", "sql_expression", "lookup")

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if not payload or any(f not in self.FIELDS for f in payload):
            raise _invalid(f"A column_mapping item changes some of {', '.join(self.FIELDS)}.")
        if payload.get("mapping_type", "unmapped") != "unmapped":
            raise _invalid("A column_mapping item can only make a mapping unmapped.")
        if self._mapping(db, workspace_id, object_id) is None:
            raise _invalid("The column mapping does not exist.")

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
        found = self._mapping(db, workspace_id, object_id, lock=True)
        warehouse = _warehouse(db, workspace_id)
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
        if mapping_type == "unmapped":
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
            {f: old[f] for f in changed},
            {f: new[f] for f in changed},
            f"{table.name}.{column.name}",
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
