"""Column mappings and the lineage derived from them (spec §6.14, stories 99, 100, 104).

An Editor maps each column of a Core or Mart table from the Layer directly below
(Core from Staging, Mart from Core) with a plain-language rule and an SQL expression in
the target dialect. The SQL text is the master: on every save it is parsed with sqlglot
and the mapping's ``LineageEdge`` rows are derived from it, replacing the previous
ones; inputs are never edited separately. Text that does not parse is saved with
``validation.unparsed = true`` and an error, and produces no edges. Inputs that do not
exist in the Layer below are refused.

Every method authorizes through the workspaces policy first. Mappings are audited
(``column_mapping`` and ``table_mapping`` entities) and carry a ``version``
(optimistic concurrency, spec §8.3), ``0`` before the first save.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .lineage_graph import reachable_edge_ids
from .lineage_sql import ColumnRef, ParsedExpression, Unparsable, parse_expression
from .tables import (
    MAPPING_TEXT_MAX_LENGTH,
    MAPPING_TYPES,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
    TableMappingRecord,
)

SOURCE_LAYER = {"core": "staging", "mart": "core"}
"""The Layer a Layer is mapped from."""
SQL_MAX_LENGTH = 20_000
MATCH_KEY_MAX = 20


@dataclass(frozen=True)
class MappingInput:
    kind: str
    table_id: uuid.UUID
    table_name: str
    column_id: uuid.UUID
    column_name: str


@dataclass(frozen=True)
class ColumnMappingView:
    id: uuid.UUID | None
    column_id: uuid.UUID
    column_name: str
    mapping_type: str
    rule_text: str
    sql_expression: str
    inputs: list[MappingInput]
    uses: list[MappingInput]
    validation: dict[str, Any]
    version: int


@dataclass(frozen=True)
class TableMappingView:
    table_id: uuid.UUID
    table_name: str
    layer: str
    source_layer: str
    integration_rule: str | None
    match_keys: list[str]
    notes: str
    version: int
    columns: list[ColumnMappingView]


@dataclass(frozen=True)
class LineageEdgeView:
    id: uuid.UUID
    kind: str
    from_type: str
    from_id: uuid.UUID
    from_label: str
    to_type: str
    to_id: uuid.UUID
    to_label: str


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(422, "invalid_mapping", message, {"field": field})


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def _conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Someone else changed this since you loaded it. Reload and try again.",
        {"current_version": current},
    )


def _text(value: Any, field: str, label: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid(field, f"The {label} must be text.")
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise _invalid(field, f"The {label} must be at most {max_length} characters.")
    return cleaned


def _snapshot(record: ColumnMappingRecord) -> dict[str, Any]:
    return {
        "mapping_type": record.mapping_type,
        "rule_text": record.rule_text,
        "sql_expression": record.sql_expression,
    }


def purge_edges_reading(db: Session, column_ids: list[uuid.UUID]) -> None:
    """Delete the edges whose source is one of the given DW columns (called when they go;
    a mapping's own edges go with the mapping)."""
    if column_ids:
        db.execute(sa.delete(LineageEdgeRecord).where(LineageEdgeRecord.from_id.in_(column_ids)))


class MappingService:
    """Column mappings, their derived lineage edges and graph queries over them."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    # --- reading ---------------------------------------------------------------------

    def get_mapping(
        self, user: User, workspace_id: uuid.UUID, table_id: uuid.UUID
    ) -> TableMappingView:
        """A table's mapping: one entry per column, ``unmapped`` until saved (any member).
        422 ``invalid_mapping`` for a Staging Table."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id)
            table = self._table(db, warehouse, table_id)
            return self._view(db, table)

    def lineage(
        self,
        user: User,
        workspace_id: uuid.UUID,
        column_id: uuid.UUID,
        direction: Literal["upstream", "downstream"],
    ) -> list[LineageEdgeView]:
        """Every edge on a path to (upstream) or from (downstream) a DW column (any
        member). 404 ``not_found`` for a column outside the Data Warehouse."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id)
            owned = db.scalar(
                sa.select(DwColumnRecord.id)
                .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
                .where(
                    DwColumnRecord.id == column_id, DwTableRecord.data_warehouse_id == warehouse.id
                )
            )
            if owned is None:
                raise _not_found("Column")
            ids = reachable_edge_ids(db, column_id, direction)
            records = list(
                db.scalars(sa.select(LineageEdgeRecord).where(LineageEdgeRecord.id.in_(ids)))
            )
            labels = self._labels(db, records)
        views = [
            LineageEdgeView(
                id=e.id,
                kind=e.kind,
                from_type=e.from_type,
                from_id=e.from_id,
                from_label=labels.get(e.from_id, ""),
                to_type=e.to_type,
                to_id=e.to_id,
                to_label=labels.get(e.to_id, ""),
            )
            for e in records
        ]
        return sorted(views, key=lambda v: (v.kind, v.from_label, v.to_label))

    # --- writing ---------------------------------------------------------------------

    def save_column_mapping(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        version: int,
        fields: Mapping[str, Any],
    ) -> ColumnMappingView:
        """Create (``version`` 0) or change a column's mapping. ``fields``: ``mapping_type``
        (direct, derived, constant, unmapped), ``rule_text`` and ``sql_expression``. Derives
        and stores the mapping's lineage edges from the SQL. 422 ``invalid_mapping``;
        409 ``version_conflict``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db, db.begin():
            warehouse = self._warehouse(db, workspace_id)
            table = self._table(db, warehouse, table_id, lock=True)
            column = db.scalars(
                sa.select(DwColumnRecord)
                .where(DwColumnRecord.id == column_id, DwColumnRecord.table_id == table.id)
                .with_for_update()
            ).first()
            if column is None:
                raise _not_found("Column")
            now = self._clock()
            table_mapping = self._table_mapping(db, table, now)
            record = db.scalars(
                sa.select(ColumnMappingRecord).where(ColumnMappingRecord.dw_column_id == column.id)
            ).first()
            if (record.version if record else 0) != version:
                raise _conflict(record.version if record else 0)
            mapping_type, rule_text, sql = self._fields(fields)
            parsed, validation = self._parse(warehouse, mapping_type, sql)
            inputs = self._resolve(db, warehouse, table, parsed) if parsed else {}
            before = _snapshot(record) if record else None
            if record is not None and before == {
                "mapping_type": mapping_type,
                "rule_text": rule_text,
                "sql_expression": sql,
            }:
                return self._column_view(db, column, record)
            if record is None:
                record = ColumnMappingRecord(
                    id=uuid.uuid4(),
                    table_mapping_id=table_mapping.id,
                    dw_column_id=column.id,
                    version=1,
                )
                db.add(record)
            else:
                record.version += 1
            record.mapping_type = mapping_type
            record.rule_text = rule_text
            record.sql_expression = sql
            record.validation = validation
            record.updated_by = user.id
            record.updated_at = now
            db.flush()
            self._replace_edges(db, record, table, parsed, inputs)
            after = _snapshot(record)
            changed = [f for f in after if before is None or before[f] != after[f]]
            if before is None or changed:
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="column_mapping",
                    entity_id=record.id,
                    old=None if before is None else {f: before[f] for f in changed},
                    new={"dw_column_id": str(column.id), **after}
                    if before is None
                    else {f: after[f] for f in changed},
                    at=now,
                )
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="column_mapping.created" if before is None else "column_mapping.updated",
                    object_type="column_mapping",
                    object_id=record.id,
                    object_label=f"{table.name}.{column.name}",
                    details={"layer": table.layer, "table_id": str(table.id)},
                    at=now,
                )
            return self._column_view(db, column, record)

    def update_table_mapping(
        self,
        user: User,
        workspace_id: uuid.UUID,
        table_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> TableMappingView:
        """Change a table's ``integration_rule``, ``match_keys`` or ``notes`` (``version`` 0
        before the first save). 409 ``version_conflict``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db, db.begin():
            warehouse = self._warehouse(db, workspace_id)
            table = self._table(db, warehouse, table_id, lock=True)
            now = self._clock()
            mapping = self._table_mapping(db, table, now)
            if mapping.version != version:
                raise _conflict(mapping.version)
            before = self._table_snapshot(mapping)
            if "integration_rule" in changes:
                rule = _text(
                    changes["integration_rule"],
                    "integration_rule",
                    "integration rule",
                    MAPPING_TEXT_MAX_LENGTH,
                )
                mapping.integration_rule = rule or None
            if "notes" in changes:
                mapping.notes = _text(changes["notes"], "notes", "notes", MAPPING_TEXT_MAX_LENGTH)
            if "match_keys" in changes:
                mapping.match_keys = self._match_keys(db, table, changes["match_keys"])
            after = self._table_snapshot(mapping)
            changed = [f for f in after if before[f] != after[f]]
            if changed:
                mapping.version += 1
                mapping.updated_at = now
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="table_mapping",
                    entity_id=mapping.id,
                    old={f: before[f] for f in changed},
                    new={f: after[f] for f in changed},
                    at=now,
                )
            db.flush()
            return self._view(db, table)

    # --- rules -----------------------------------------------------------------------

    def _fields(self, fields: Mapping[str, Any]) -> tuple[str, str, str]:
        mapping_type = fields.get("mapping_type")
        if mapping_type not in MAPPING_TYPES:
            raise _invalid("mapping_type", f"The type must be one of {', '.join(MAPPING_TYPES)}.")
        rule_text = _text(fields.get("rule_text"), "rule_text", "rule", MAPPING_TEXT_MAX_LENGTH)
        sql = _text(fields.get("sql_expression"), "sql_expression", "SQL", SQL_MAX_LENGTH)
        if mapping_type == "unmapped" and sql:
            raise _invalid("sql_expression", "An unmapped column has no SQL.")
        if mapping_type != "unmapped" and not sql:
            raise _invalid("sql_expression", f"A {mapping_type} mapping needs its SQL expression.")
        return mapping_type, rule_text, sql

    def _parse(
        self, warehouse: DataWarehouseRecord, mapping_type: str, sql: str
    ) -> tuple[ParsedExpression | None, dict[str, Any]]:
        """The parsed SQL (``None`` when there is none or it does not parse) and the
        mapping's validation."""
        if not sql:
            return None, {"unparsed": False, "errors": []}
        try:
            parsed = parse_expression(sql, warehouse.target_platform)
        except Unparsable as exc:
            error = {"code": "unparsed", "message": f"The SQL does not parse: {exc}"}
            return None, {"unparsed": True, "errors": [error]}
        if mapping_type == "direct" and not parsed.is_column:
            raise _invalid("sql_expression", "A direct mapping is one column, as table.column.")
        if mapping_type == "constant" and (parsed.value or parsed.uses):
            raise _invalid("sql_expression", "A constant mapping reads no columns.")
        return parsed, {"unparsed": False, "errors": []}

    def _resolve(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        target: DwTableRecord,
        parsed: ParsedExpression,
    ) -> dict[ColumnRef, tuple[DwTableRecord, DwColumnRecord]]:
        """Each referenced column, which must exist in the Layer directly below."""
        refs = [*parsed.value, *parsed.uses]
        layer = SOURCE_LAYER[target.layer]
        for ref in refs:
            if ref.table is None:
                raise _invalid(
                    "sql_expression",
                    f"Write {ref.column} as table.column, naming a {layer} table.",
                )
        found: dict[ColumnRef, tuple[DwTableRecord, DwColumnRecord]] = {}
        if refs:
            rows = db.execute(
                sa.select(DwTableRecord, DwColumnRecord)
                .join(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
                .where(
                    DwTableRecord.data_warehouse_id == warehouse.id,
                    DwTableRecord.layer == layer,
                    sa.func.lower(DwTableRecord.name).in_(
                        {r.table.lower() for r in refs if r.table}
                    ),
                )
            ).all()
            index = {(t.name.lower(), c.name.lower()): (t, c) for t, c in rows}
            for ref in refs:
                hit = index.get((str(ref.table).lower(), ref.column.lower()))
                if hit is None:
                    raise _invalid(
                        "sql_expression",
                        f"{ref.table}.{ref.column} is not a column of a {layer} table; "
                        f"{target.layer} tables are mapped from the {layer} Layer.",
                    )
                found[ref] = hit
        return found

    def _match_keys(self, db: Session, table: DwTableRecord, value: Any) -> list[str]:
        if not isinstance(value, list) or len(value) > MATCH_KEY_MAX:
            raise _invalid(
                "match_keys", f"Match keys are a list of at most {MATCH_KEY_MAX} columns."
            )
        names = {c.name.lower(): c.name for c in self._columns(db, table.id)}
        keys: list[str] = []
        for item in value:
            if not isinstance(item, str) or item.strip().lower() not in names:
                raise _invalid("match_keys", f"{item!r} is not a column of this table.")
            canonical = names[item.strip().lower()]
            if canonical not in keys:
                keys.append(canonical)
        return keys

    # --- storing ---------------------------------------------------------------------

    def _replace_edges(
        self,
        db: Session,
        record: ColumnMappingRecord,
        table: DwTableRecord,
        parsed: ParsedExpression | None,
        inputs: dict[ColumnRef, tuple[DwTableRecord, DwColumnRecord]],
    ) -> None:
        db.execute(sa.delete(LineageEdgeRecord).where(LineageEdgeRecord.mapping_id == record.id))
        if parsed is None:
            return
        for ref in parsed.value:
            db.add(
                LineageEdgeRecord(
                    id=uuid.uuid4(),
                    kind="value",
                    from_type="dw_column",
                    from_id=inputs[ref][1].id,
                    to_type="dw_column",
                    to_id=record.dw_column_id,
                    mapping_id=record.id,
                )
            )
        for ref in parsed.uses:
            db.add(
                LineageEdgeRecord(
                    id=uuid.uuid4(),
                    kind="uses",
                    from_type="dw_column",
                    from_id=inputs[ref][1].id,
                    to_type="dw_table",
                    to_id=table.id,
                    mapping_id=record.id,
                )
            )
        db.flush()

    def _table_mapping(
        self, db: Session, table: DwTableRecord, now: datetime
    ) -> TableMappingRecord:
        """The table's mapping, made on first use (an empty one, ``version`` 0 until the
        table-level fields are saved)."""
        mapping = db.scalars(
            sa.select(TableMappingRecord)
            .where(TableMappingRecord.dw_table_id == table.id)
            .with_for_update()
        ).first()
        if mapping is None:
            mapping = TableMappingRecord(
                id=uuid.uuid4(),
                dw_table_id=table.id,
                integration_rule=None,
                match_keys=[],
                notes="",
                created_at=now,
                updated_at=now,
                version=0,
            )
            db.add(mapping)
            db.flush()
        return mapping

    @staticmethod
    def _table_snapshot(mapping: TableMappingRecord) -> dict[str, Any]:
        return {
            "integration_rule": mapping.integration_rule,
            "match_keys": list(mapping.match_keys),
            "notes": mapping.notes,
        }

    # --- loading ---------------------------------------------------------------------

    def _warehouse(self, db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord:
        warehouse = db.scalars(
            sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
        ).first()
        if warehouse is None:
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
        if table.layer not in SOURCE_LAYER:
            raise _invalid("table", "Staging Tables mirror the source; they have no mapping here.")
        return table

    def _columns(self, db: Session, table_id: uuid.UUID) -> list[DwColumnRecord]:
        return list(
            db.scalars(
                sa.select(DwColumnRecord)
                .where(DwColumnRecord.table_id == table_id)
                .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
            )
        )

    def _labels(self, db: Session, edges: list[LineageEdgeRecord]) -> dict[uuid.UUID, str]:
        """``table.column`` for each DW column and the name for each DW table of the edges."""
        ids = {i for e in edges for i in (e.from_id, e.to_id)}
        labels: dict[uuid.UUID, str] = {}
        for column_id, column_name, table_name in db.execute(
            sa.select(DwColumnRecord.id, DwColumnRecord.name, DwTableRecord.name)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .where(DwColumnRecord.id.in_(ids))
        ):
            labels[column_id] = f"{table_name}.{column_name}"
        for table_id, name in db.execute(
            sa.select(DwTableRecord.id, DwTableRecord.name).where(DwTableRecord.id.in_(ids))
        ):
            labels[table_id] = name
        return labels

    def _inputs(
        self, db: Session, mapping_id: uuid.UUID
    ) -> tuple[list[MappingInput], list[MappingInput]]:
        rows = db.execute(
            sa.select(
                LineageEdgeRecord.kind,
                DwTableRecord.id,
                DwTableRecord.name,
                DwColumnRecord.id,
                DwColumnRecord.name,
            )
            .join(DwColumnRecord, DwColumnRecord.id == LineageEdgeRecord.from_id)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .where(LineageEdgeRecord.mapping_id == mapping_id)
            .order_by(DwTableRecord.name, DwColumnRecord.ordinal)
        ).all()
        values = [MappingInput(*row) for row in rows if row[0] == "value"]
        uses = [MappingInput(*row) for row in rows if row[0] == "uses"]
        return values, uses

    def _column_view(
        self, db: Session, column: DwColumnRecord, record: ColumnMappingRecord | None
    ) -> ColumnMappingView:
        if record is None:
            return ColumnMappingView(
                id=None,
                column_id=column.id,
                column_name=column.name,
                mapping_type="unmapped",
                rule_text="",
                sql_expression="",
                inputs=[],
                uses=[],
                validation={"unparsed": False, "errors": []},
                version=0,
            )
        values, uses = self._inputs(db, record.id)
        return ColumnMappingView(
            id=record.id,
            column_id=column.id,
            column_name=column.name,
            mapping_type=record.mapping_type,
            rule_text=record.rule_text,
            sql_expression=record.sql_expression,
            inputs=values,
            uses=uses,
            validation=record.validation,
            version=record.version,
        )

    def _view(self, db: Session, table: DwTableRecord) -> TableMappingView:
        mapping = db.scalars(
            sa.select(TableMappingRecord).where(TableMappingRecord.dw_table_id == table.id)
        ).first()
        records = {
            r.dw_column_id: r
            for r in db.scalars(
                sa.select(ColumnMappingRecord)
                .join(DwColumnRecord, DwColumnRecord.id == ColumnMappingRecord.dw_column_id)
                .where(DwColumnRecord.table_id == table.id)
            )
        }
        return TableMappingView(
            table_id=table.id,
            table_name=table.name,
            layer=table.layer,
            source_layer=SOURCE_LAYER[table.layer],
            integration_rule=mapping.integration_rule if mapping else None,
            match_keys=list(mapping.match_keys) if mapping else [],
            notes=mapping.notes if mapping else "",
            version=mapping.version if mapping else 0,
            columns=[
                self._column_view(db, c, records.get(c.id)) for c in self._columns(db, table.id)
            ],
        )
