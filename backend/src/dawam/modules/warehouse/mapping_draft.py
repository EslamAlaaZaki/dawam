"""AI mapping drafts (spec §6.9 "AI generation", story 101): the assistant's
``draft_mappings`` tool drafts the mappings of Core and Mart tables as a Change Set.

``MappingDraftService.inputs`` reads what the drafter needs for the target tables (their
columns and what is mapped already, the existing branches, and the columns of the Layer
below) and ``plan`` turns the model's answer into Change Set items: one ``mapping_draft``
item per branch, nesting that branch's column mappings (and lookups). The model names tables
and columns; DAWAM maps them back to ids. Every branch and mapping is checked exactly as a
save checks it (the SQL parses, every input is a column of the Layer below, a lookup names
the dimension's natural-key inputs), and nothing is stored until the member accepts the item.
A column that is mapped already is never overwritten: the draft skips it and says so.

``MappingDraftHandler`` is the ``changesets`` engine's handler for ``mapping_draft`` items.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import AppliedChange, ProposedItem
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError

from .mapping_service import SOURCE_LAYER, MappingService, is_system_column
from .model_service import MODEL_LAYERS
from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

OBJECT_TYPE = "mapping_draft"
SCOPE_KIND = "mapping_draft"
MAX_INPUT_TABLES = 10
"""Target tables whose inputs are read at once; a bigger request is read in turns."""
MAX_REQUEST_TABLES = 100
"""Target tables one request (a job) covers."""
MAX_SOURCE_TABLES = 300
MAX_SOURCE_COLUMNS = 120
_BRANCH_KEYS = ("name", "driving_input", "joins", "filters", "group_by", "having")
_COLUMN_KEYS = {"column_id", "column_name", "mapping_type", "rule_text", "sql_expression", "lookup"}


@dataclass(frozen=True)
class DraftColumn:
    """A column's mapping as the model describes it."""

    column: str
    mapping_type: str
    sql_expression: str = ""
    rule_text: str = ""
    lookup: Mapping[str, Any] | None = None
    """A Core fact FK's ``{nk_inputs: [staging_table.column], as_of_input?}``."""


@dataclass(frozen=True)
class DraftBranch:
    name: str
    driving_input: str
    joins: str = ""
    filters: str = ""
    group_by: str | None = None
    having: str | None = None
    columns: Sequence[DraftColumn] = ()


@dataclass(frozen=True)
class DraftTable:
    table: str
    """The target table's name, optionally ``layer.name``."""
    branches: Sequence[DraftBranch] = ()


@dataclass(frozen=True)
class DraftNote:
    code: str
    object: str
    message: str


@dataclass(frozen=True)
class MappingDraftPlan:
    items: list[ProposedItem]
    table_ids: list[uuid.UUID]
    notes: list[DraftNote] = field(default_factory=list)
    origin: str = "ai"

    @property
    def scope(self) -> dict[str, Any]:
        return {"kind": SCOPE_KIND, "table_ids": sorted(str(i) for i in self.table_ids)}


def _invalid(message: str, problems: Sequence[str] = ()) -> ApiError:
    return ApiError(422, "invalid_mapping_draft", message, {"problems": list(problems)})


def _type(data_type: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data_type.items() if v is not None}


@dataclass
class _Derived:
    mapping_type: str
    rule_text: str
    sql: str
    lookup: dict[str, Any] | None
    validation: dict[str, Any]
    parsed: Any
    inputs: dict[Any, Any]
    dimension: DwTableRecord | None


def _derive(
    helpers: MappingService,
    db: Session,
    warehouse: DataWarehouseRecord,
    table: DwTableRecord,
    column: DwColumnRecord,
    raw: Mapping[str, Any],
) -> _Derived:
    """What a save of this mapping in a branch derives: checked fields, validation, the
    parsed SQL and the inputs it reads (``ApiError`` 422 for what a save refuses)."""
    mapping_type, rule_text, sql = helpers._fields(raw, in_branch=True, column=column)
    lookup: dict[str, Any] | None = None
    dimension: DwTableRecord | None = None
    if mapping_type == "lookup":
        lookup, parsed, dimension = helpers._lookup(db, warehouse, table, column, raw.get("lookup"))
        validation: dict[str, Any] = {"unparsed": False, "errors": []}
    else:
        if raw.get("lookup") is not None:
            raise ApiError(422, "invalid_mapping", "Only a lookup mapping has a lookup.")
        parsed, validation = helpers._parse(warehouse, mapping_type, sql)
    inputs = helpers._resolve(db, warehouse, table, parsed) if parsed else {}
    return _Derived(mapping_type, rule_text, sql, lookup, validation, parsed, inputs, dimension)


class MappingDraftService:
    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService) -> None:
        self._engine = engine
        self._workspaces = workspaces
        # The private MappingService SQL helpers (parse, resolve, check a branch) are shared on
        # purpose, so a draft is checked exactly as a save is; they authorize nothing.
        self._helpers = MappingService(engine, workspaces=workspaces, clock=None)  # type: ignore[arg-type]

    # -- reading --------------------------------------------------------------------

    def resolve(
        self,
        user: User,
        workspace_id: uuid.UUID,
        tables: Sequence[str] = (),
        layer: str | None = None,
    ) -> list[str]:
        """``layer.name`` of the target tables a request names: ``tables`` (a name or
        ``layer.name``), or every table of ``layer``. Owners and editors. 404 ``not_set_up``;
        422 ``invalid_mapping_draft`` for a name that is no Core or Mart table."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._helpers._warehouse(db, workspace_id)
            if layer is not None:
                found = self._layer_tables(db, warehouse, layer)
            else:
                found = [self._target(db, warehouse, name) for name in tables]
            if not found:
                raise _invalid("There is no table to draft mappings for.")
            if len(found) > MAX_REQUEST_TABLES:
                raise _invalid(f"Draft at most {MAX_REQUEST_TABLES} tables in one request.")
            return [f"{t.layer}.{t.name}" for t in found]

    def inputs(
        self, user: User, workspace_id: uuid.UUID, tables: Sequence[str] = ()
    ) -> dict[str, Any]:
        """What the drafter reads for the target ``tables`` (owners and editors): each
        table's columns with what is mapped already, its branches, and the columns of the
        Layer below. 404 ``not_set_up``; 422 ``invalid_mapping_draft``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._helpers._warehouse(db, workspace_id)
            targets = [self._target(db, warehouse, name) for name in tables]
            if not targets:
                raise _invalid("Name the tables to draft mappings for.")
            if len(targets) > MAX_INPUT_TABLES:
                raise _invalid(f"Read the inputs of at most {MAX_INPUT_TABLES} tables at a time.")
            layers = {SOURCE_LAYER[t.layer] for t in targets}
            return {
                "platform": warehouse.target_platform,
                "targets": [self._described(db, t) for t in targets],
                "sources": self._sources(db, warehouse, layers),
            }

    def _described(self, db: Session, table: DwTableRecord) -> dict[str, Any]:
        view = self._helpers._view(db, table)
        mapped: dict[uuid.UUID, set[str]] = {}
        for branch in view.branches:
            for c in branch.columns:
                if c.mapping_type != "unmapped":
                    mapped.setdefault(c.column_id, set()).add(branch.name)
        names = {
            t.id: t.name
            for t in db.scalars(
                sa.select(DwTableRecord).where(
                    DwTableRecord.data_warehouse_id == table.data_warehouse_id
                )
            )
        }
        out: dict[str, Any] = {
            "layer": table.layer,
            "name": table.name,
            "kind": table.kind,
            "maps_from": SOURCE_LAYER[table.layer],
        }
        for name in ("description", "grain", "fact_type", "scd_type"):
            if getattr(table, name):
                out[name] = getattr(table, name)
        if table.is_aggregate:
            out["is_aggregate"] = True
        out["branches"] = [
            {
                "name": b.name,
                "driving_input": b.driving_input,
                "joins": b.joins,
                "filters": b.filters,
                "group_by": b.group_by,
                "having": b.having,
            }
            for b in view.branches
        ]
        columns = []
        for column in self._helpers._columns(db, table.id):
            if is_system_column(column):
                continue  # DAWAM maps its own columns
            entry: dict[str, Any] = {
                k: v
                for k, v in {
                    "name": column.name,
                    "type": _type(column.data_type),
                    "role": column.role,
                    "nullable": column.is_nullable,
                    "additivity": column.additivity,
                    "description": column.description or None,
                    "references": names.get(column.references_table_id)
                    if column.references_table_id
                    else None,
                }.items()
                if v is not None
            }
            if column.id in mapped:
                entry["mapped_in_branches"] = sorted(mapped[column.id])
            columns.append(entry)
        out["columns"] = columns
        return out

    def _sources(
        self, db: Session, warehouse: DataWarehouseRecord, layers: set[str]
    ) -> list[dict[str, Any]]:
        rows = db.execute(
            sa.select(DwTableRecord, DwColumnRecord)
            .join(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer.in_(layers),
                DwTableRecord.status == "present",
                DwColumnRecord.status == "present",
            )
            .order_by(DwTableRecord.layer, DwTableRecord.name, DwColumnRecord.ordinal)
        ).all()
        tables: dict[tuple[str, str], dict[str, Any]] = {}
        for table, column in rows:
            if column.role == "audit" or column.is_system:
                continue
            entry = tables.setdefault(
                (table.layer, table.name),
                {"layer": table.layer, "name": table.name, "kind": table.kind, "columns": []},
            )
            if len(entry["columns"]) < MAX_SOURCE_COLUMNS:
                entry["columns"].append(
                    {"name": column.name, "type": _type(column.data_type), "role": column.role}
                )
        return list(tables.values())[:MAX_SOURCE_TABLES]

    @staticmethod
    def _layer_tables(
        db: Session, warehouse: DataWarehouseRecord, layer: str
    ) -> list[DwTableRecord]:
        if layer not in MODEL_LAYERS:
            raise _invalid("The layer is core or mart.")
        return list(
            db.scalars(
                sa.select(DwTableRecord)
                .where(
                    DwTableRecord.data_warehouse_id == warehouse.id,
                    DwTableRecord.layer == layer,
                    DwTableRecord.status == "present",
                    DwTableRecord.kind != "generated",
                )
                .order_by(DwTableRecord.name)
            )
        )

    @staticmethod
    def _target(db: Session, warehouse: DataWarehouseRecord, name: str) -> DwTableRecord:
        layer, _, bare = name.strip().rpartition(".")
        query = sa.select(DwTableRecord).where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer.in_(MODEL_LAYERS),
            sa.func.lower(DwTableRecord.name) == bare.lower(),
        )
        if layer:
            query = query.where(DwTableRecord.layer == layer.lower())
        found = list(db.scalars(query))
        if not found:
            raise _invalid(f"{name!r} is no Core or Mart table.")
        if len(found) > 1:
            raise _invalid(f"{name!r} is in both Layers; write it as core.{bare} or mart.{bare}.")
        if found[0].kind == "generated":
            raise _invalid(f"{name!r} is maintained by DAWAM; it has no mapping to draft.")
        return found[0]

    # -- planning -------------------------------------------------------------------

    def plan(
        self, user: User, workspace_id: uuid.UUID, drafts: Sequence[DraftTable]
    ) -> MappingDraftPlan:
        """The Change Set items for the model's ``drafts`` (owners and editors). Nothing is
        changed. 422 ``invalid_mapping_draft`` lists every problem found."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        problems: list[str] = []
        notes: list[DraftNote] = []
        items: list[ProposedItem] = []
        table_ids: list[uuid.UUID] = []
        with Session(self._engine) as db:
            warehouse = self._helpers._warehouse(db, workspace_id)
            for draft in drafts:
                try:
                    table = self._target(db, warehouse, draft.table)
                except ApiError as exc:
                    problems.append(exc.message)
                    continue
                if table.id in table_ids:
                    problems.append(f"{draft.table!r} is drafted twice.")
                    continue
                table_ids.append(table.id)
                names: set[str] = set()
                for branch in draft.branches:
                    if branch.name.strip().lower() in names:
                        problems.append(f"{table.name}: two branches are called {branch.name!r}.")
                        continue
                    names.add(branch.name.strip().lower())
                    item = self._branch_item(
                        db, warehouse, table, branch, len(items), problems, notes
                    )
                    if item is not None:
                        items.append(item)
        if problems:
            raise _invalid(
                f"{len(problems)} problem(s) in the draft; nothing was collected.", problems
            )
        return MappingDraftPlan(items, table_ids, notes)

    def _branch_item(
        self,
        db: Session,
        warehouse: DataWarehouseRecord,
        table: DwTableRecord,
        branch: DraftBranch,
        n: int,
        problems: list[str],
        notes: list[DraftNote],
    ) -> ProposedItem | None:
        where = f"{table.layer}.{table.name}: {branch.name}"
        existing = (
            db.execute(
                sa.select(MappingBranchRecord)
                .join(
                    TableMappingRecord,
                    TableMappingRecord.id == MappingBranchRecord.table_mapping_id,
                )
                .where(
                    TableMappingRecord.dw_table_id == table.id,
                    sa.func.lower(MappingBranchRecord.name) == branch.name.strip().lower(),
                )
            )
            .scalars()
            .first()
        )
        try:
            if existing is not None:
                notes.append(
                    DraftNote(
                        "branch_exists",
                        where,
                        "The branch exists; its definition is kept and only unmapped columns "
                        "are drafted.",
                    )
                )
                values = {f: getattr(existing, f) for f in _BRANCH_KEYS}
            else:
                values, _ = self._helpers._branch_values(
                    db,
                    warehouse,
                    table,
                    {
                        "name": branch.name,
                        "driving_input": branch.driving_input,
                        "joins": branch.joins,
                        "filters": branch.filters,
                        "group_by": branch.group_by,
                        "having": branch.having,
                    },
                )
        except ApiError as exc:
            problems.append(f"{where}: {exc.message}")
            return None
        columns = {c.name.lower(): c for c in self._helpers._columns(db, table.id)}
        done: set[uuid.UUID] = set()
        mapped_already: set[uuid.UUID] = set()
        if existing is not None:
            mapped_already = set(
                db.scalars(
                    sa.select(ColumnMappingRecord.dw_column_id).where(
                        ColumnMappingRecord.branch_id == existing.id,
                        ColumnMappingRecord.mapping_type != "unmapped",
                    )
                )
            )
        payload_columns: list[dict[str, Any]] = []
        for draft in branch.columns:
            at = f"{where}.{draft.column}"
            column = columns.get(draft.column.strip().lower())
            if column is None:
                problems.append(f"{at}: the table has no such column.")
                continue
            if is_system_column(column):
                notes.append(
                    DraftNote("system_column", at, "DAWAM maps system columns itself; skipped.")
                )
                continue
            if column.id in done:
                problems.append(f"{at}: mapped twice in the branch.")
                continue
            done.add(column.id)
            if column.id in mapped_already:
                notes.append(
                    DraftNote("already_mapped", at, "Already mapped in this branch; kept as is.")
                )
                continue
            entry = {
                "mapping_type": draft.mapping_type,
                "rule_text": draft.rule_text,
                "sql_expression": draft.sql_expression,
                "lookup": dict(draft.lookup) if draft.lookup else None,
            }
            try:
                derived = _derive(self._helpers, db, warehouse, table, column, entry)
            except ApiError as exc:
                problems.append(f"{at}: {exc.message}")
                continue
            payload_columns.append(
                {
                    "column_id": str(column.id),
                    "column_name": column.name,
                    "mapping_type": derived.mapping_type,
                    "rule_text": derived.rule_text,
                    "sql_expression": derived.sql,
                    "lookup": derived.lookup,
                }
            )
        left = [
            c.name
            for c in columns.values()
            if not is_system_column(c) and c.id not in done and c.id not in mapped_already
        ]
        if left:
            notes.append(
                DraftNote(
                    "unmapped_columns",
                    where,
                    "The draft leaves these columns unmapped: " + ", ".join(sorted(left)),
                )
            )
        if not payload_columns and existing is not None:
            return None
        return ProposedItem(
            key=f"map{n}",
            object_type=OBJECT_TYPE,
            operation="create",
            label=f"{table.layer}.{table.name}: {values['name']}",
            payload={
                "table_id": str(table.id),
                "branch": {**values, "id": str(existing.id) if existing is not None else None},
                "columns": payload_columns,
            },
        )


def _invalid_item(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


class MappingDraftHandler:
    """Creates a branch (or fills an existing one) with the column mappings the draft
    proposes, deriving the lineage exactly as a save does."""

    object_type = OBJECT_TYPE
    wants_actor = True

    def required_action(self, operation: str) -> Action:
        if operation != "create":
            raise ValueError("A mapping draft is only created.")
        return Action.EDIT_DW_SCHEMA

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        branch = payload.get("branch")
        columns = payload.get("columns")
        if (
            _uuid(payload.get("table_id")) is None
            or not isinstance(branch, Mapping)
            or not all(isinstance(branch.get(k), str) for k in ("name", "driving_input"))
            or not isinstance(columns, list)
            or not all(
                isinstance(c, Mapping)
                and _uuid(c.get("column_id")) is not None
                and set(c) <= _COLUMN_KEYS
                for c in columns
            )
        ):
            raise _invalid_item("A mapping draft carries its table, branch and column mappings.")
        if self._table(db, workspace_id, _uuid(payload["table_id"])) is None:
            raise _invalid_item("The table of the mapping draft does not exist.")

    def current_values(
        self, db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID, fields: Sequence[str]
    ) -> dict[str, Any] | None:  # pragma: no cover - creates have no base values
        return None

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> AppliedChange:
        table = self._table(db, workspace_id, _uuid(payload["table_id"]), lock=True)
        warehouse = db.scalars(
            sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
        ).first()
        if table is None or warehouse is None:
            raise ApiError(409, "version_conflict", "The table no longer exists.")
        helpers = MappingService(db.get_bind(), workspaces=None, clock=None)  # type: ignore[arg-type]
        mapping = helpers._table_mapping(db, table, at)
        spec = payload["branch"]
        branch_id = _uuid(spec.get("id"))
        created = branch_id is None
        if created:
            values, parts = helpers._branch_values(db, warehouse, table, spec)
            last = db.scalar(
                sa.select(sa.func.max(MappingBranchRecord.ordinal)).where(
                    MappingBranchRecord.table_mapping_id == mapping.id
                )
            )
            branch = MappingBranchRecord(
                id=uuid.uuid4(),
                table_mapping_id=mapping.id,
                ordinal=(last or 0) + 1,
                created_at=at,
                updated_at=at,
                version=1,
                **values,
            )
            db.add(branch)
            db.flush()
            helpers._replace_branch_edges(db, warehouse, table, branch, parts)
        else:
            branch = db.scalars(
                sa.select(MappingBranchRecord)
                .where(
                    MappingBranchRecord.id == branch_id,
                    MappingBranchRecord.table_mapping_id == mapping.id,
                )
                .with_for_update()
            ).first()
            if branch is None:
                raise ApiError(409, "version_conflict", "The mapping branch no longer exists.")
        mapped: list[str] = []
        mapped_ids: list[str] = []
        skipped: list[str] = []
        for entry in payload["columns"]:
            column = db.scalars(
                sa.select(DwColumnRecord)
                .where(
                    DwColumnRecord.id == _uuid(entry["column_id"]),
                    DwColumnRecord.table_id == table.id,
                )
                .with_for_update()
            ).first()
            if column is None or is_system_column(column):
                skipped.append(str(entry.get("column_name")))
                continue
            record = db.scalars(
                sa.select(ColumnMappingRecord).where(
                    ColumnMappingRecord.dw_column_id == column.id,
                    ColumnMappingRecord.branch_id == branch.id,
                )
            ).first()
            if record is not None and record.mapping_type != "unmapped":
                skipped.append(column.name)  # mapped since the draft: never overwritten
                continue
            derived = _derive(helpers, db, warehouse, table, column, entry)
            if record is None:
                record = ColumnMappingRecord(
                    id=uuid.uuid4(),
                    table_mapping_id=mapping.id,
                    branch_id=branch.id,
                    dw_column_id=column.id,
                    version=1,
                )
                db.add(record)
            else:
                record.version += 1
            record.mapping_type = derived.mapping_type
            record.rule_text = derived.rule_text
            record.sql_expression = derived.sql
            record.lookup = derived.lookup
            record.validation = derived.validation
            record.updated_by = actor_id
            record.updated_at = at
            db.flush()
            helpers._replace_edges(
                db, record, table, derived.parsed, derived.inputs, derived.dimension
            )
            mapped.append(column.name)
            mapped_ids.append(str(column.id))
        # ``mapped_column_ids`` lets propagation follow newly mapped columns after the commit.
        new: dict[str, Any] = {"mapped_columns": mapped, "mapped_column_ids": mapped_ids}
        if created:
            new |= {f: getattr(branch, f) for f in _BRANCH_KEYS}
        if skipped:
            new["skipped_columns"] = skipped
        return AppliedChange(
            "mapping_branch",
            branch.id,
            None if created else {},
            new,
            f"{table.name}: {branch.name}",
        )

    @staticmethod
    def _table(
        db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID | None, *, lock: bool = False
    ) -> DwTableRecord | None:
        if table_id is None:
            return None
        query = (
            sa.select(DwTableRecord)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DataWarehouseRecord.workspace_id == workspace_id,
                DwTableRecord.layer.in_(MODEL_LAYERS),
                DwTableRecord.id == table_id,
            )
        )
        if lock:
            query = query.with_for_update(of=DwTableRecord)
        return db.scalars(query).first()
