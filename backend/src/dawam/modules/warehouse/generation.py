"""AI generation of the Core and Mart Layers (spec §6.9 "AI generation", "Generation
provenance", "Regeneration"; story 88).

The assistant's ``generate_dw_schema`` tool reads what the generator needs with
``GenerationService.inputs`` (the staging tables, the existing Core and Mart objects, each
behind a short opaque handle, the date dimension and what users deleted) and turns the model's
answer into Change Set items with ``plan``. The model never sees an id or a
``generation_key``: it names existing objects by handle, and DAWAM maps handles back, so an AI
rename stays a rename. An item with an unknown or missing handle becomes a create, flagged
"possible duplicate of X" when an existing table has its name or nearly its columns (or, for a
column, its table already has one of that name). DAWAM issues a ``generation_key`` to every
table and column the generator creates and stores the last generated value of each field
(``generated``); a field the user overrode (``edited_fields``) is changed only by a conflict
item the user accepts. A generated object a user deleted has a Tombstone and is never proposed
again. Every fact can rely on a date dimension: when there is none, the plan creates the
built-in one. A regeneration may be limited to one existing table (``table``, story 98a): the
model then reads what that table's mapping reads and describes only that table, and the
Change Set's scope names it. Only an item of a generation Change Set applies as the
generator's (an item of any other Change Set, e.g. from ``propose_changes``, is a user edit).

A plan that is collected always applies (#617): planning checks the rules the handlers
enforce, then dry-runs the items through the handlers in a savepoint it always rolls back.
DAWAM adds what is mechanical: the audit columns of every Core and Mart table, the names of
new foreign keys (after the key they point to) and ``is_conformed`` (two or more tables
reference the dimension); the model never sends them.

``DwTableHandler`` and ``DwColumnHandler`` are the ``changesets`` engine's handlers for
``dw_table`` and ``dw_column`` items (Core and Mart creates and updates).
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import AppliedChange, ProposedItem
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError

from .calendar import DATE_KEY
from .mapping_service import MappingService
from .model_service import (
    GENERATED_COLUMN_FIELDS,
    GENERATED_TABLE_FIELDS,
    HOUSEKEEPING_NAMES,
    MODEL_KINDS,
    MODEL_LAYERS,
    _column_snapshot,
    _identifier,
    _plain,
    _rules,
    _table_snapshot,
    audit_columns,
    check_column_shape,
    editing_rules,
    generated_table_name,
    key_name,
    mark_edited,
)
from .platforms import max_identifier_length
from .regeneration import table_inputs
from .service import NamingRules
from .staging_naming import stable_hash
from .tables import (
    DELETED,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    TombstoneRecord,
)

TABLE = "dw_table"
COLUMN = "dw_column"
AUDIT_COLUMNS = "audit_columns"
"""``payload[AUDIT_COLUMNS]`` of a table create or update: DAWAM adds the audit columns the
naming rules name (a column that already has one of those names is kept)."""
SCOPE: Mapping[str, Any] = {"kind": "dw_generation"}
"""The scope of a generation Change Set: a newer generation supersedes a pending one."""
PROVENANCE = "provenance"
GENERATION = "generation"
"""``payload[PROVENANCE]`` of an item the generator proposed: accepting it takes the AI's
value (the field is no longer an override). Any other accepted change to a generated object
counts as a user edit."""
DATE_HANDLE = "date"
"""What the model calls the date dimension when there is none yet."""
SIMILAR_COLUMNS = 0.8
"""Column-set similarity (Jaccard) above which a new table is a possible duplicate."""
MAX_TABLES = 300
_KIND_ORDER = {"generated": 0, "dimension": 1, "bridge": 2, "fact": 3}


@dataclass(frozen=True)
class GeneratedColumn:
    """A column as the model describes it."""

    name: str
    data_type: Mapping[str, Any]
    role: str
    handle: str | None = None
    is_nullable: bool = True
    additivity: str | None = None
    scd_type_override: int | None = None
    references: str | None = None
    """The handle or name of the dimension a foreign key points at (``date`` for the date
    dimension)."""
    role_name: str | None = None
    description: str = ""
    semantic_type: str | None = None
    lookup: Mapping[str, Any] | None = None
    """A Core fact FK's ``{nk_inputs: [staging_table.column], as_of_input?}``."""


@dataclass(frozen=True)
class GeneratedTable:
    """A table as the model describes it."""

    layer: str
    name: str
    kind: str
    handle: str | None = None
    description: str = ""
    grain: str | None = None
    fact_type: str | None = None
    is_aggregate: bool = False
    scd_type: int | None = None
    columns: Sequence[GeneratedColumn] = ()
    """``is_conformed`` is not the model's: DAWAM derives it from the references."""


@dataclass(frozen=True)
class GenerationNote:
    code: str
    object: str
    message: str


@dataclass(frozen=True)
class GenerationPlan:
    origin: str
    """``ai`` for a first generation, ``regeneration`` once generated objects exist."""
    items: list[ProposedItem]
    notes: list[GenerationNote] = field(default_factory=list)
    table_id: uuid.UUID | None = None
    """Set for a regeneration of only this table (story 98a)."""

    @property
    def conflicts(self) -> int:
        return sum(1 for i in self.items if i.is_conflict)

    @property
    def scope(self) -> dict[str, Any]:
        """The Change Set's scope: a table's regeneration supersedes only a pending one of
        that table, never the whole model's."""
        if self.table_id is None:
            return dict(SCOPE)
        return {**SCOPE, "table_id": str(self.table_id)}


def _invalid(message: str, problems: Sequence[str] = ()) -> ApiError:
    return ApiError(422, "invalid_generation", message, {"problems": list(problems)})


def _reserved_message(name: str) -> str:
    return f"DAWAM adds a column named {name} to this table itself; leave it out."


def _fk_collision(name: str) -> str:
    return (
        f"DAWAM names this foreign key {name}, after the key it points to, and another "
        "column of the table has that name: rename that column, or give the foreign key a "
        "role name."
    )


def _handles(prefix: str, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """A short opaque handle per id, stable for the id and unique among ``ids``."""
    out: dict[uuid.UUID, str] = {}
    taken: set[str] = set()
    for object_id in sorted(ids):
        digest = base64.b32encode(hashlib.sha1(object_id.bytes).digest()).decode().lower()
        for length in range(5, len(digest) + 1):
            handle = f"{prefix}{digest[:length]}"
            if handle not in taken:
                break
        taken.add(handle)
        out[object_id] = handle
    return out


def _type(data_type: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data_type.items() if v is not None}


@dataclass
class _Model:
    """The Core and Mart objects as they are now, with their handles."""

    warehouse: DataWarehouseRecord
    tables: list[DwTableRecord]
    columns: dict[uuid.UUID, list[DwColumnRecord]]
    handle_of: dict[uuid.UUID, str]
    tombstoned: set[str]
    staging: dict[str, set[str]]
    """Staging table name -> its column names (lower case)."""

    @property
    def by_handle(self) -> dict[str, DwTableRecord | DwColumnRecord]:
        found: dict[str, DwTableRecord | DwColumnRecord] = {
            self.handle_of[t.id]: t for t in self.tables
        }
        for columns in self.columns.values():
            for c in columns:
                if c.id in self.handle_of:
                    found[self.handle_of[c.id]] = c
        return found

    def date_dimension(self) -> DwTableRecord | None:
        name = generated_table_name(_rules(self.warehouse), "date").lower()
        for t in self.tables:  # a hand-made one of that name serves too
            if (
                t.kind in ("generated", "dimension")
                and t.layer == "core"
                and t.name.lower() == name
            ):
                return t
        return None


def _load(db: Session, warehouse: DataWarehouseRecord) -> _Model:
    tables = list(
        db.scalars(
            sa.select(DwTableRecord)
            .where(
                DwTableRecord.data_warehouse_id == warehouse.id,
                DwTableRecord.layer.in_(MODEL_LAYERS),
                DwTableRecord.status != DELETED,
            )
            .order_by(DwTableRecord.layer, sa.func.lower(DwTableRecord.name))
        )
    )
    columns: dict[uuid.UUID, list[DwColumnRecord]] = {t.id: [] for t in tables}
    for column in db.scalars(
        sa.select(DwColumnRecord)
        .where(DwColumnRecord.table_id.in_(list(columns)))
        .order_by(DwColumnRecord.ordinal, DwColumnRecord.id)
    ):
        columns[column.table_id].append(column)
    handle_of = _handles("t", [t.id for t in tables])
    handle_of.update(
        _handles("c", [c.id for cs in columns.values() for c in cs if not c.is_system])
    )
    tombstoned = {
        name.lower()
        for name in db.scalars(
            sa.select(TombstoneRecord.name).where(
                TombstoneRecord.data_warehouse_id == warehouse.id,
                TombstoneRecord.object_type.in_((TABLE, COLUMN)),
                TombstoneRecord.name.is_not(None),
            )
        )
        if name
    }
    staging: dict[str, set[str]] = {}
    for table_name, column_name in db.execute(
        sa.select(DwTableRecord.name, DwColumnRecord.name)
        .join(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
        .where(
            DwTableRecord.data_warehouse_id == warehouse.id,
            DwTableRecord.layer == "staging",
            DwTableRecord.status != DELETED,
        )
    ):
        staging.setdefault(table_name.lower(), set()).add(column_name.lower())
    return _Model(warehouse, tables, columns, handle_of, tombstoned, staging)


def _scoped_table(model: _Model, ref: str) -> DwTableRecord:
    """The Core or Mart table a table-scoped regeneration is limited to: by handle, by
    ``layer.name`` or by a name only one table has. 422 ``invalid_generation``."""
    found = model.by_handle.get(ref)
    if not isinstance(found, DwTableRecord):
        wanted = ref.strip().lower()
        matches = [
            t for t in model.tables if wanted in (t.name.lower(), f"{t.layer}.{t.name}".lower())
        ]
        found = matches[0] if len(matches) == 1 else None
    if found is None or found.kind == "generated":
        raise _invalid(
            f"No single Core or Mart fact, dimension or bridge is {ref!r}; give its handle or "
            "its layer and name."
        )
    return found


def _mapping_inputs(db: Session, table_id: uuid.UUID) -> list[str]:
    """What a table's mapping reads now, as ``table.column``: the user's mapping decisions
    a table-scoped regeneration follows."""
    ids = table_inputs(db, table_id)
    rows = db.execute(
        sa.select(DwTableRecord.name, DwColumnRecord.name)
        .join(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
        .where(DwColumnRecord.id.in_(ids))
    )
    return sorted(f"{t}.{c}" for t, c in rows)


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _free_name(name: str, taken: set[str], limit: int) -> str:
    if name.lower() not in taken:
        return name
    for n in range(2, 1000):
        suffix = f"_{n}"
        candidate = name[: limit - len(suffix)] + suffix
        if candidate.lower() not in taken:
            return candidate
    raise _invalid(f"No free name for {name}.")  # pragma: no cover


def _dawam_adds(rules: NamingRules, platform: str) -> dict[str, Any]:
    """What DAWAM adds to the tables the model describes, so the model never adds it."""
    history = list(HOUSEKEEPING_NAMES)
    return {
        "dimension": {
            "key": key_name("<table name>", "dimension", platform),
            "history_columns": history,
            "history_note": "Added to a dimension with scd_type 2 or an attribute with "
            "scd_type_override 2.",
        },
        "bridge": {"key": key_name("<table name>", "bridge", platform)},
        "every_core_and_mart_table": [name for name, _, _ in audit_columns(rules)],
        "foreign_key_names": "DAWAM names every new foreign key after the key it points to "
        "(dim_customer_key), or <role_name>_<key name> with a role name; the name you send is "
        "replaced.",
        "is_conformed": "DAWAM derives it: a dimension is conformed when two or more tables "
        "reference it. Do not send it.",
    }


class GenerationService:
    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService) -> None:
        self._engine = engine
        self._workspaces = workspaces

    def _warehouse(self, db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord:
        warehouse = db.scalars(
            sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
        ).first()
        if warehouse is None:
            raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
        return warehouse

    # -- reading --------------------------------------------------------------------

    def inputs(
        self, user: User, workspace_id: uuid.UUID, *, table: str | None = None
    ) -> dict[str, Any]:
        """What the generator reads (owners and editors): the target platform and naming
        rules, the staging tables, the existing Core and Mart objects behind handles, the
        date dimension and the generated objects users deleted. With ``table`` (a handle,
        ``layer.name`` or name), also the one table a regeneration is limited to and what
        its mapping reads (``regenerate_only``). 404 ``not_set_up``; 422
        ``invalid_generation`` for an unknown ``table``."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id)
            model = _load(db, warehouse)
            only = _scoped_table(model, table) if table is not None else None
            staging = db.execute(
                sa.select(DwTableRecord, DwColumnRecord)
                .join(DwColumnRecord, DwColumnRecord.table_id == DwTableRecord.id)
                .where(
                    DwTableRecord.data_warehouse_id == warehouse.id,
                    DwTableRecord.layer == "staging",
                    DwTableRecord.status == "present",
                    DwColumnRecord.status == "present",
                )
                .order_by(DwTableRecord.name, DwColumnRecord.ordinal)
            ).all()
            staged: dict[str, list[dict[str, Any]]] = {}
            for table, column in staging:
                if column.role == "audit":
                    continue
                staged.setdefault(table.name, []).append(
                    {
                        "name": column.name,
                        "type": _type(column.data_type),
                        "nullable": column.is_nullable,
                    }
                )
            date = model.date_dimension()
            rules = _rules(warehouse)
            return {
                "platform": warehouse.target_platform,
                "naming_rules": {
                    "case_style": rules.case_style,
                    "dimension_prefix": rules.dimension_prefix,
                    "fact_prefix": rules.fact_prefix,
                    "bridge_prefix": rules.bridge_prefix,
                },
                "staging": [{"name": n, "columns": cs} for n, cs in staged.items()],
                "dawam_adds": _dawam_adds(rules, warehouse.target_platform),
                "existing": [self._existing(model, t) for t in model.tables],
                "date_dimension": {
                    "handle": model.handle_of[date.id] if date else DATE_HANDLE,
                    "name": date.name if date else generated_table_name(rules, "date"),
                    "exists": date is not None,
                },
                "deleted_by_users": sorted(model.tombstoned),
                **(
                    {
                        "regenerate_only": {
                            "handle": model.handle_of[only.id],
                            "layer": only.layer,
                            "name": only.name,
                            "mapping_inputs": _mapping_inputs(db, only.id),
                        }
                    }
                    if only is not None
                    else {}
                ),
            }

    @staticmethod
    def _existing(model: _Model, table: DwTableRecord) -> dict[str, Any]:
        out: dict[str, Any] = {
            "handle": model.handle_of[table.id],
            "layer": table.layer,
            "name": table.name,
            "kind": table.kind,
            "generated": table.generation_key is not None,
        }
        for name in ("description", "grain", "fact_type", "scd_type"):
            if getattr(table, name):
                out[name] = getattr(table, name)
        if table.is_aggregate:
            out["is_aggregate"] = True
        if table.is_conformed:
            out["is_conformed"] = True
        if table.kind == "generated":
            return out  # DAWAM maintains its columns
        out["dawam_columns"] = [
            c.name
            for c in model.columns[table.id]
            if c.is_system or (c.role == "sk" and table.kind in ("dimension", "bridge"))
        ]
        out["columns"] = [
            {
                k: v
                for k, v in {
                    "handle": model.handle_of[c.id],
                    "name": c.name,
                    "type": _type(c.data_type),
                    "role": c.role,
                    "additivity": c.additivity,
                    "references": model.handle_of.get(c.references_table_id)
                    if c.references_table_id
                    else None,
                    "role_name": c.role_name,
                }.items()
                if v is not None
            }
            for c in model.columns[table.id]
            if not c.is_system
        ]
        return out

    # -- planning -------------------------------------------------------------------

    def plan(
        self,
        user: User,
        workspace_id: uuid.UUID,
        tables: Sequence[GeneratedTable],
        *,
        table: str | None = None,
    ) -> GenerationPlan:
        """The Change Set items that bring the DW Schema to the model's ``tables`` (owners
        and editors). Nothing is changed. 422 ``invalid_generation`` lists every problem the
        model must fix (a fact without a grain, a measure without an additivity, a foreign
        key to nothing); notes report what DAWAM decided on its own. With ``table`` the
        regeneration is limited to that one existing table: ``tables`` describes only it,
        by its handle."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        if not tables:
            raise _invalid("Describe at least one table.")
        if len(tables) > MAX_TABLES:
            raise _invalid(f"Generate at most {MAX_TABLES} tables at once.")
        with Session(self._engine) as db:
            warehouse = self._warehouse(db, workspace_id)
            model = _load(db, warehouse)
            only = _scoped_table(model, table) if table is not None else None
            if only is not None and [t.handle for t in tables] != [model.handle_of[only.id]]:
                raise _invalid(
                    f"This regeneration is limited to {only.layer}.{only.name}: describe only "
                    f"that table, with its handle {model.handle_of[only.id]!r}."
                )
            only_id = only.id if only is not None else None
            plan = _Planner(db, model, scoped=only is not None).plan(tables)
            return replace(plan, table_id=only_id) if only_id is not None else plan


@dataclass
class _Target:
    """A table a foreign key may point at: an existing one, or one this plan creates."""

    id: uuid.UUID
    layer: str
    kind: str
    is_conformed: bool
    scd_type: int | None
    nk_count: int
    item_key: str | None
    """The create item it depends on, when the plan creates it."""
    key: str | None = None
    """The name of its key, which a foreign key to it is named after."""
    record: DwTableRecord | None = None
    derived_conformed: bool = False
    """Two or more tables reference it once the plan is applied."""

    @property
    def keeps_conformed(self) -> bool:
        """A member set the flag of this existing table (by hand, or on a table they made):
        it is kept, as other hand-edited fields are."""
        record = self.record
        return record is not None and (
            record.generation_key is None or "is_conformed" in record.edited_fields
        )


class _Planner:
    def __init__(self, db: Session, model: _Model, *, scoped: bool = False) -> None:
        self._db = db
        self._model = model
        self._scoped = scoped
        """A regeneration of one table: the other existing tables are left as they are."""
        self._warehouse = model.warehouse
        self._platform = model.warehouse.target_platform
        self._rules = editing_rules(lambda: datetime.min)
        self._limit = max_identifier_length(self._platform)  # type: ignore[arg-type]
        self._audit = [name for name, _, _ in audit_columns(_rules(model.warehouse))]
        self._problems: list[str] = []
        self._notes: list[GenerationNote] = []
        self._renamed: set[tuple[str, str]] = set()  # (layer, name) a rename of the plan takes
        self._by_id: dict[uuid.UUID, _Target] = {}

    def _problem(self, where: str, message: str) -> None:
        self._problems.append(f"{where}: {message}")

    def _note(self, code: str, where: str, message: str) -> None:
        self._notes.append(GenerationNote(code, where, message))

    def plan(self, specs: Sequence[GeneratedTable]) -> GenerationPlan:
        model = self._model
        by_handle = model.by_handle
        matched: list[tuple[GeneratedTable, DwTableRecord | None]] = []
        claimed: set[uuid.UUID] = set()
        for spec in specs:
            where = f"{spec.layer}.{spec.name}"
            record = None
            if spec.handle:
                found = by_handle.get(spec.handle)
                if not isinstance(found, DwTableRecord):
                    self._note(
                        "unknown_handle",
                        where,
                        f"No table has the handle {spec.handle!r}; it is proposed as new.",
                    )
                elif found.id in claimed:
                    self._note(
                        "handle_used_twice",
                        where,
                        f"Another table already used the handle {spec.handle!r}; this one "
                        "is proposed as new.",
                    )
                elif found.layer != spec.layer or found.kind != spec.kind:
                    self._problem(
                        where,
                        f"{spec.handle} is a {found.layer} {found.kind}; a table keeps its "
                        "layer and kind (leave the handle out to propose a new table).",
                    )
                    continue
                else:
                    record = found
                    claimed.add(found.id)
            matched.append((spec, record))

        creates = self._creates(matched)
        targets = self._targets(matched, creates)
        self._conform(matched, creates, targets)
        date_item = self._date_item(matched, targets)
        items: list[ProposedItem] = []
        if date_item is not None:
            items.append(date_item)
        # Layer by layer, creates before updates: a Mart table may need a Core dimension
        # created, or made conformed, before it.
        planned = {r.id for _, r in matched if r is not None}
        for layer in MODEL_LAYERS:
            new_items: list[tuple[str, int, ProposedItem]] = []
            for index, (spec, record) in enumerate(matched):
                if record is None and index in creates and spec.layer == layer:
                    item = self._create_table(spec, creates[index], targets)
                    if item is not None:
                        new_items.append((spec.kind, index, item))
            new_items.sort(key=lambda t: (_KIND_ORDER[t[0]], t[1]))
            items += [i for *_, i in new_items]
            for spec, record in matched:
                if record is not None and record.layer == layer:
                    items += self._update_table(spec, record, targets)
            if not self._scoped:
                for table in model.tables:
                    if table.layer == layer and table.id not in planned:
                        items += self._untouched_table(table)
        items = self._unconform_last(self._after_conformed(items))
        if not self._problems:
            self._dry_run(items)
        if self._problems:
            raise _invalid(
                f"The model has {len(self._problems)} problem(s); fix them and call again.",
                self._problems,
            )
        origin = "regeneration" if any(t.generation_key for t in model.tables) else "ai"
        return GenerationPlan(origin, items, self._notes)

    # -- what DAWAM derives and adds ------------------------------------------------

    def _conform(
        self,
        matched: Sequence[tuple[GeneratedTable, DwTableRecord | None]],
        creates: Mapping[int, tuple[uuid.UUID, str, list[dict[str, str]]]],
        targets: Mapping[str, _Target],
    ) -> None:
        """Derive each dimension's ``is_conformed`` from the model as it will be after the
        plan: conformed when two or more tables reference it. A member's flag is kept."""
        model = self._model
        refs: dict[uuid.UUID, set[uuid.UUID]] = {}

        def add(dimension_id: uuid.UUID | None, table_id: uuid.UUID) -> None:
            if dimension_id is not None and dimension_id != table_id:
                refs.setdefault(dimension_id, set()).add(table_id)

        in_plan: set[uuid.UUID] = set()
        for index, (spec, record) in enumerate(matched):
            if record is not None:
                own = record.id
            elif index in creates:
                own = creates[index][0]
            else:
                continue
            in_plan.add(own)
            handles = (
                {model.handle_of[c.id] for c in model.columns[record.id] if not c.is_system}
                if record is not None
                else set()
            )
            for column in spec.columns:
                if column.role != "fk" or not column.references:
                    continue
                if (
                    record is not None
                    and column.handle not in handles
                    and f"{record.layer}.{record.name}.{column.name}".lower() in model.tombstoned
                ):
                    continue  # a user deleted it: the plan does not add it again
                target = targets.get(column.references.lower())
                add(target.id if target else None, own)
            if record is not None:
                kept = {c.handle for c in spec.columns if c.handle}
                for existing in model.columns[record.id]:
                    if model.handle_of.get(existing.id) not in kept:
                        add(existing.references_table_id, record.id)
        for table in model.tables:
            if table.id not in in_plan:
                for existing in model.columns[table.id]:
                    add(existing.references_table_id, table.id)
        for target in self._by_id.values():
            if target.kind != "dimension":
                continue
            target.derived_conformed = len(refs.get(target.id, ())) >= 2
            if target.keeps_conformed or (self._scoped and target.id not in in_plan):
                # A member's flag stands; a one-table regeneration changes no other table.
                assert target.record is not None
                target.is_conformed = target.record.is_conformed
            else:
                target.is_conformed = target.derived_conformed

    def _reserved(
        self,
        kind: str,
        name: str,
        history: bool,
        existing: Sequence[DwColumnRecord] | None = None,
    ) -> dict[str, str]:
        """The columns DAWAM adds to a table (lower-cased name -> name): its key, the SCD2
        housekeeping columns of a dimension that keeps history and the audit columns; for
        an existing table, also the ones it already added."""
        out: dict[str, str] = {}
        key = key_name(name, kind, self._platform)
        if key is not None and existing is None:
            out[key.lower()] = key
        if kind == "dimension" and history:
            out |= {n.lower(): n for n in HOUSEKEEPING_NAMES}
        out |= {n.lower(): n for n in self._audit}
        for column in existing or ():
            if column.is_system or (column.role == "sk" and kind in ("dimension", "bridge")):
                out[column.name.lower()] = column.name
        return out

    def _audit_item(self, record: DwTableRecord) -> ProposedItem | None:
        """DAWAM's audit columns for an existing table that lacks them. A column that
        already has one of those names is kept, and gets no duplicate."""
        if record.kind == "generated":
            return None
        have = {c.name.lower(): c for c in self._model.columns[record.id]}
        missing: list[str] = []
        for name in self._audit:
            column = have.get(name.lower())
            if column is None:
                missing.append(name)
            elif not column.is_system:
                self._note(
                    "audit_column_kept",
                    f"{record.layer}.{record.name}.{column.name}",
                    "The table already has a column of this name, so DAWAM keeps it and adds "
                    "no audit column of that name.",
                )
        if not missing:
            return None
        return ProposedItem(
            key=f"a:{record.id}",
            object_type=TABLE,
            operation="update",
            object_id=record.id,
            label=f"{record.layer}.{record.name} (audit columns {', '.join(missing)})",
            payload={AUDIT_COLUMNS: True, PROVENANCE: GENERATION},
            base_values={},
        )

    def _untouched_table(self, record: DwTableRecord) -> list[ProposedItem]:
        """An existing table the model left out still gets what DAWAM derives and adds."""
        items: list[ProposedItem] = []
        target = self._by_id.get(record.id)
        if record.kind == "dimension" and target is not None and not target.keeps_conformed:
            plain, conflict = self._split(record, {"is_conformed": target.derived_conformed})
            label = f"{record.layer}.{record.name}"
            items += self._items(TABLE, record, label, plain, conflict, [])
        audit = self._audit_item(record)
        if audit is not None:
            items.append(audit)
        return items

    @staticmethod
    def _after_conformed(items: list[ProposedItem]) -> list[ProposedItem]:
        """A Mart item that references a Core dimension this plan makes conformed depends on
        that item, so it never applies before it."""
        conformed = {
            str(i.object_id): i.key
            for i in items
            if i.object_type == TABLE
            and i.operation == "update"
            and not i.is_conflict
            and i.payload.get("is_conformed") is True
        }
        if not conformed:
            return items
        out: list[ProposedItem] = []
        for item in items:
            if item.label.startswith("mart."):
                refs = [item.payload.get("references_table_id")] + [
                    c.get("references_table_id") for c in item.payload.get("columns") or []
                ]
                extra = [conformed[r] for r in refs if r in conformed]
                extra = [k for k in dict.fromkeys(extra) if k not in item.depends_on]
                if extra:
                    item = replace(item, depends_on=[*item.depends_on, *extra])
            out.append(item)
        return out

    @staticmethod
    def _unconform_last(items: list[ProposedItem]) -> list[ProposedItem]:
        """A dimension stops being conformed only after every other item: a Mart item of
        the plan may first move the reference that kept it conformed elsewhere."""
        out: list[ProposedItem] = []
        last: list[ProposedItem] = []
        for item in items:
            if (
                item.object_type == TABLE
                and item.operation == "update"
                and not item.is_conflict
                and item.payload.get("is_conformed") is False
            ):
                rest = {k: v for k, v in item.payload.items() if k != "is_conformed"}
                base = dict(item.base_values or {})
                last.append(
                    replace(
                        item,
                        key=f"{item.key}:unconform",
                        payload={"is_conformed": False, PROVENANCE: GENERATION},
                        base_values={"is_conformed": base.pop("is_conformed", True)},
                        depends_on=[],
                    )
                )
                if set(rest) - {PROVENANCE}:
                    out.append(replace(item, payload=rest, base_values=base))
                continue
            out.append(item)
        return out + last

    def _dry_run(self, items: Sequence[ProposedItem]) -> None:
        """Apply the items with the real handlers, in the Change Set engine's order, and roll
        everything back: whatever an apply would refuse becomes a problem. Planning leaves no
        trace and holds no lock once it returns.

        No savepoint is ever released: SQLAlchemy runs a session's commit hooks on a
        RELEASE too, and the score trigger's hook then scores the warehouse from another
        connection, which waits forever on the rows this transaction holds. A savepoint is
        rolled back when its item fails and otherwise stays open under the next one."""
        db = self._db
        workspace_id = self._warehouse.workspace_id
        handlers: dict[str, DwTableHandler | DwColumnHandler] = {
            TABLE: DwTableHandler(),
            COLUMN: DwColumnHandler(),
        }
        at = datetime.now(UTC)
        failed: set[str] = set()
        try:
            for item in items:
                if any(d in failed for d in item.depends_on):
                    failed.add(item.key)
                    continue
                handler = handlers[item.object_type]
                where = item.label.split(" (")[0]
                savepoint = db.begin_nested()
                try:
                    handler.validate(db, workspace_id, item.operation, item.object_id, item.payload)
                    handler.apply(
                        db,
                        workspace_id,
                        item.operation,
                        item.object_id,
                        item.payload,
                        at=at,
                        scope=SCOPE,
                    )
                    db.flush()
                except ApiError as exc:
                    savepoint.rollback()
                    failed.add(item.key)
                    column = exc.details.get("column")
                    self._problem(f"{where}.{column}" if column else where, exc.message)
                except IntegrityError:
                    savepoint.rollback()
                    failed.add(item.key)
                    self._problem(where, "A name it uses is already taken.")
        finally:
            db.rollback()  # the whole transaction, with every savepoint: no trace, no lock

    # -- tables to create -----------------------------------------------------------

    def _creates(
        self, matched: Sequence[tuple[GeneratedTable, DwTableRecord | None]]
    ) -> dict[int, tuple[uuid.UUID, str, list[dict[str, str]]]]:
        """For each table to create: its id, its (free) name and its review flags."""
        model = self._model
        taken = {
            layer: {t.name.lower() for t in model.tables if t.layer == layer}
            for layer in MODEL_LAYERS
        }
        for spec, record in matched:  # a new table never takes the name a rename gives
            if record is not None and spec.name.lower() != record.name.lower():
                taken[spec.layer].add(spec.name.lower())
        unclaimed = [t for t in model.tables if t.id not in {r.id for _, r in matched if r}]
        out: dict[int, tuple[uuid.UUID, str, list[dict[str, str]]]] = {}
        asked: set[str] = set()
        for index, (spec, record) in enumerate(matched):
            if record is not None:
                continue
            where = f"{spec.layer}.{spec.name}"
            try:
                name = _identifier(self._platform, spec.name, "name", "table name")
            except ApiError as exc:
                self._problem(where, exc.message)
                continue
            if where.lower() in model.tombstoned:
                self._note(
                    "deleted_by_user",
                    where,
                    "A user deleted this generated table, so it is not proposed again.",
                )
                continue
            if where.lower() in asked:
                self._problem(where, "Two new tables have this name.")
                continue
            asked.add(where.lower())
            flags: list[dict[str, str]] = []
            duplicate = self._duplicate_of(spec, name, unclaimed)
            if duplicate is not None:
                label = f"{duplicate.layer}.{duplicate.name}"
                flags.append(
                    {"code": "possible_duplicate", "message": f"Possible duplicate of {label}."}
                )
                self._note(
                    "possible_duplicate",
                    where,
                    f"Proposed as new and flagged as a possible duplicate of {label} (give "
                    "its handle to change that table instead).",
                )
            name = _free_name(name, taken[spec.layer], self._limit)
            taken[spec.layer].add(name.lower())
            out[index] = (uuid.uuid4(), name, flags)
        return out

    def _duplicate_of(
        self, spec: GeneratedTable, name: str, candidates: Sequence[DwTableRecord]
    ) -> DwTableRecord | None:
        same_layer = [t for t in candidates if t.layer == spec.layer]
        for table in same_layer:
            if table.name.lower() == name.lower():
                return table
        wanted = {c.name.lower() for c in spec.columns if c.role != "sk"}
        best, score = None, 0.0
        for table in same_layer:
            have = {
                c.name.lower()
                for c in self._model.columns[table.id]
                if not c.is_system and c.role != "sk"
            }
            similarity = _jaccard(wanted, have) if min(len(wanted), len(have)) >= 2 else 0.0
            if similarity >= SIMILAR_COLUMNS and similarity > score:
                best, score = table, similarity
        return best

    # -- what a foreign key may point at -------------------------------------------

    def _targets(
        self,
        matched: Sequence[tuple[GeneratedTable, DwTableRecord | None]],
        creates: Mapping[int, tuple[uuid.UUID, str, list[dict[str, str]]]],
    ) -> dict[str, _Target]:
        """Every way the model may name a table (handle, ``layer.name``, plain name),
        lower-cased. Tables of the plan win over existing ones of the same name."""
        model = self._model
        out: dict[str, _Target] = {}

        def add(target: _Target, layer: str, *names: str) -> None:
            for name in names:
                out.setdefault(f"{layer}.{name}".lower(), target)
                out.setdefault(name.lower(), target)

        for index, (spec, record) in enumerate(matched):
            nk = sum(1 for c in spec.columns if c.role == "nk")
            scd = spec.scd_type if spec.kind == "dimension" else None
            if spec.kind == "dimension" and scd is None:
                scd = 1
            if record is not None:
                target = _Target(
                    record.id,
                    record.layer,
                    record.kind,
                    record.is_conformed,
                    scd,
                    nk,
                    None,
                    key=self._key_of(record),
                    record=record,
                )
                out[model.handle_of[record.id]] = target
                add(target, spec.layer, spec.name, record.name)
            elif index in creates:
                table_id, name, _ = creates[index]
                target = _Target(
                    table_id,
                    spec.layer,
                    spec.kind,
                    False,
                    scd,
                    nk,
                    f"t:{table_id}",
                    key=key_name(name, spec.kind, self._platform),
                )
                add(target, spec.layer, spec.name, name)
            else:
                continue
            self._by_id[target.id] = target
        for table in model.tables:
            target = self._by_id.get(table.id)
            if target is None:
                nk = sum(1 for c in model.columns[table.id] if c.role == "nk" and not c.is_system)
                target = _Target(
                    table.id,
                    table.layer,
                    table.kind,
                    table.is_conformed,
                    table.scd_type,
                    nk,
                    None,
                    key=self._key_of(table),
                    record=table,
                )
                self._by_id[table.id] = target
            out.setdefault(model.handle_of[table.id], target)
            add(target, table.layer, table.name)
        date = model.date_dimension()
        if date is not None:
            out.setdefault(DATE_HANDLE, out[model.handle_of[date.id]])
        else:
            name = generated_table_name(_rules(self._warehouse), "date")
            target = _Target(uuid.uuid4(), "core", "generated", True, None, 0, "date", key=DATE_KEY)
            out[DATE_HANDLE] = target
            add(target, "core", name)
        return out

    def _key_of(self, table: DwTableRecord) -> str | None:
        """The name of an existing dimension's key (its surrogate key column)."""
        for column in self._model.columns[table.id]:
            if column.role == "sk":
                return column.name
        return None

    def _date_item(
        self,
        matched: Sequence[tuple[GeneratedTable, DwTableRecord | None]],
        targets: Mapping[str, _Target],
    ) -> ProposedItem | None:
        """The built-in date dimension, when a foreign key needs it and there is none."""
        date = targets[DATE_HANDLE]
        if date.item_key is None:
            return None
        wanted = any(
            c.references and targets.get(c.references.lower()) is date
            for spec, _ in matched
            for c in spec.columns
        )
        if not wanted:
            return None
        self._note(
            "date_dimension_added",
            "core.date",
            "There was no date dimension, so the built-in one is part of the Change Set.",
        )
        return ProposedItem(
            key="date",
            object_type=TABLE,
            operation="create",
            label=f"core.{generated_table_name(_rules(self._warehouse), 'date')}",
            payload={
                "builtin": "date",
                "layer": "core",
                "id": str(date.id),
                "generation_key": uuid.uuid4().hex,
                PROVENANCE: GENERATION,
            },
        )

    def _reference(
        self, where: str, layer: str, own_id: uuid.UUID, column: GeneratedColumn, targets: Mapping
    ) -> _Target | None:
        if column.role != "fk":
            if column.references:
                self._problem(where, "Only a foreign key (role fk) references a table.")
            return None
        if not column.references:
            self._problem(where, "A foreign key names the dimension it references.")
            return None
        target = targets.get(column.references.lower())
        if target is None:
            self._problem(where, f"References {column.references!r}, which is no table.")
            return None
        if target.id == own_id:
            self._problem(where, "A table cannot reference itself.")
            return None
        if target.kind not in ("dimension", "generated"):
            self._problem(where, f"References {column.references!r}, which is not a dimension.")
            return None
        if layer == "mart" and target.layer == "core" and not target.is_conformed:
            self._problem(
                where,
                f"{column.references!r} is a Core dimension that is not conformed (DAWAM "
                "makes a dimension conformed when two or more tables reference it): reference "
                "it from Core as well, or model it in the Mart Layer.",
            )
            return None
        if target.layer != layer and not (layer == "mart" and target.layer == "core"):
            self._problem(
                where,
                "A table references dimensions of its own Layer; a Mart table may also "
                "reference Core conformed dimensions.",
            )
            return None
        return target

    def _lookup(
        self,
        where: str,
        layer: str,
        kind: str,
        column: GeneratedColumn,
        target: _Target | None,
    ) -> dict[str, Any] | None:
        if not column.lookup:
            return None
        if layer != "core" or kind != "fact" or target is None:
            self._problem(where, "Only a Core fact's foreign key has a lookup.")
            return None
        if target.kind == "generated":
            self._note(
                "lookup_not_needed",
                where,
                "The date dimension's key is computed from the date; no lookup is kept.",
            )
            return None
        raw = column.lookup
        nk_inputs = list(raw.get("nk_inputs") or [])
        as_of = raw.get("as_of_input") or None
        for text in [*nk_inputs, *([as_of] if as_of else [])]:
            table, _, name = str(text).partition(".")
            if name.lower() not in self._model.staging.get(table.lower(), set()):
                self._problem(where, f"The lookup input {text!r} is no staging column.")
                return None
        if not nk_inputs or len(nk_inputs) != target.nk_count:
            self._problem(
                where,
                f"The dimension has {target.nk_count} natural-key column(s); the lookup "
                "gives one staging input for each, in order.",
            )
            return None
        if target.scd_type == 2 and not as_of:
            self._problem(where, "The dimension is SCD2: give the as_of_input date column.")
            return None
        spec: dict[str, Any] = {"nk_inputs": nk_inputs}
        if as_of:
            spec["as_of_input"] = as_of
        return spec

    # -- creates --------------------------------------------------------------------

    def _column_payload(
        self,
        where: str,
        layer: str,
        kind: str,
        own_id: uuid.UUID,
        column: GeneratedColumn,
        targets: Mapping[str, _Target],
        *,
        new: bool,
    ) -> tuple[dict[str, Any], str | None, _Target | None] | None:
        """A checked column, the create item its foreign key needs and the table it points
        at. A ``new`` column (one the model adds) also follows what DAWAM decides: no audit
        columns, foreign keys named by DAWAM, a Core fact's foreign key with a lookup."""
        if new and column.role == "audit":
            self._problem(where, "DAWAM adds the audit columns itself; leave this one out.")
            return None
        if column.role == "measure" and not column.additivity:
            self._problem(where, "A measure declares its additivity.")
        target = self._reference(where, layer, own_id, column, targets)
        try:
            fields = self._rules._column_fields(
                self._warehouse,
                {
                    "name": column.name,
                    "data_type": dict(column.data_type),
                    "role": column.role,
                    "is_nullable": column.is_nullable,
                    "additivity": column.additivity,
                    "scd_type_override": column.scd_type_override,
                    "role_name": column.role_name,
                    "description": column.description or "",
                    "semantic_type": column.semantic_type,
                },
                partial=False,
            )
            check_column_shape(kind, {**fields, "references_table_id": None})
        except ApiError as exc:
            self._problem(where, exc.message)
            return None
        fields["references_table_id"] = str(target.id) if target else None
        if new and target is not None and not self._name_foreign_key(where, target, fields):
            return None
        lookup = self._lookup(where, layer, kind, column, target)
        if lookup is not None:
            fields["lookup"] = lookup
        elif (
            new
            and not column.lookup
            and layer == "core"
            and kind == "fact"
            and target is not None
            and target.kind != "generated"
            and target is not targets.get(DATE_HANDLE)
        ):
            self._problem(
                where,
                "A Core fact's foreign key gives a `lookup`: the staging natural-key column(s) "
                "of the dimension, in order (and the as-of date column for an SCD2 one).",
            )
        return fields, target.item_key if target else None, target

    def _name_foreign_key(self, where: str, target: _Target, fields: dict[str, Any]) -> bool:
        """DAWAM names a new foreign key after the key it points to, prefixed with its role
        name; over the platform's limit it is shortened with a stable hash and flagged."""
        key = target.key or fields["name"]
        wanted = f"{fields['role_name']}_{key}" if fields["role_name"] else key
        name = wanted
        if len(wanted.encode()) > self._limit:
            suffix = "_" + stable_hash(wanted)
            name = wanted[: self._limit - len(suffix)].rstrip("_") + suffix
            fields["review_flags"] = [
                {
                    "code": "truncated",
                    "message": f"The name {wanted} is over the platform's {self._limit}-byte "
                    "limit.",
                }
            ]
        try:
            name = _identifier(self._platform, name, "name", "column name")
        except ApiError as exc:
            self._problem(where, exc.message)
            return False
        if name != fields["name"]:
            self._note(
                "foreign_key_named",
                where,
                f"DAWAM names this foreign key {name}, after the key it points to.",
            )
        fields["name"] = name
        return True

    def _create_table(
        self,
        spec: GeneratedTable,
        create: tuple[uuid.UUID, str, list[dict[str, str]]],
        targets: Mapping[str, _Target],
    ) -> ProposedItem | None:
        table_id, name, flags = create
        where = f"{spec.layer}.{spec.name}"
        if spec.kind == "fact" and (not spec.grain or not spec.fact_type):
            self._problem(where, "A fact states its grain and fact type.")
        history = spec.scd_type == 2 or any(c.scd_type_override == 2 for c in spec.columns)
        reserved = self._reserved(spec.kind, name, history)
        columns: list[dict[str, Any]] = []
        depends: list[str] = []
        names: dict[str, str] = {}  # lower-cased name -> role
        roles: set[tuple[uuid.UUID, str]] = set()  # (dimension, role name) of each FK
        for column in spec.columns:
            if column.role == "sk" and spec.kind in ("dimension", "bridge"):
                continue  # DAWAM adds the surrogate (or group) key
            col_where = f"{where}.{column.name}"
            if column.name.lower() in reserved:
                self._problem(col_where, _reserved_message(column.name))
                continue
            checked = self._column_payload(
                col_where, spec.layer, spec.kind, table_id, column, targets, new=True
            )
            if checked is None:
                continue
            fields, needs, target = checked
            if target is not None:
                role = (fields["role_name"] or "").lower()
                if (target.id, role) in roles:
                    self._problem(
                        col_where,
                        "This table already has a foreign key to that dimension"
                        + (
                            f" in the role {fields['role_name']!r}."
                            if role
                            else "; give each a role name."
                        ),
                    )
                    continue
                roles.add((target.id, role))
            final = fields["name"].lower()
            if final in reserved or final in names:
                if column.role == "fk" or names.get(final) == "fk":
                    self._problem(col_where, _fk_collision(fields["name"]))
                else:
                    self._problem(col_where, "Two columns have this name.")
                continue
            names[final] = column.role
            fields["generation_key"] = uuid.uuid4().hex
            columns.append(fields)
            if needs and needs not in depends:
                depends.append(needs)
        payload: dict[str, Any] = {
            "id": str(table_id),
            "generation_key": uuid.uuid4().hex,
            "layer": spec.layer,
            "kind": spec.kind,
            "name": name,
            "description": spec.description or "",
            "review_flags": flags,
            "columns": columns,
            AUDIT_COLUMNS: True,
            PROVENANCE: GENERATION,
        }
        if spec.kind == "fact":
            payload |= {
                "grain": spec.grain,
                "fact_type": spec.fact_type,
                "is_aggregate": spec.is_aggregate,
            }
        if spec.kind == "dimension":
            payload |= {
                "scd_type": 1 if spec.scd_type is None else spec.scd_type,
                "is_conformed": self._by_id[table_id].is_conformed,
            }
        label = f"{spec.layer}.{name}"
        if flags:
            label += f" ({flags[0]['message'].rstrip('.').lower()})"
        return ProposedItem(
            key=f"t:{table_id}",
            object_type=TABLE,
            operation="create",
            label=label,
            payload=payload,
            depends_on=depends,
        )

    # -- updates --------------------------------------------------------------------

    def _split(
        self,
        record: DwTableRecord | DwColumnRecord,
        wanted: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The changed fields: those it may change, and the user's overrides (conflicts).
        Every field of an object a user made by hand is theirs."""
        plain: dict[str, Any] = {}
        conflict: dict[str, Any] = {}
        generated = record.generated or {}
        for name, value in wanted.items():
            current = _plain(getattr(record, name))
            if value == current:
                continue
            if record.generation_key is None:
                conflict[name] = value
            elif name in record.edited_fields:
                if name in generated and generated[name] == value:
                    continue  # the AI still says what it said; the user's value stands
                conflict[name] = value
            else:
                plain[name] = value
        return plain, conflict

    def _items(
        self,
        object_type: str,
        record: DwTableRecord | DwColumnRecord,
        label: str,
        plain: Mapping[str, Any],
        conflict: Mapping[str, Any],
        depends: Sequence[str],
    ) -> list[ProposedItem]:
        out = []
        for fields, is_conflict in ((plain, False), (conflict, True)):
            if not fields:
                continue
            out.append(
                ProposedItem(
                    key=f"u:{record.id}{':conflict' if is_conflict else ''}",
                    object_type=object_type,
                    operation="update",
                    object_id=record.id,
                    label=label,
                    payload={**fields, PROVENANCE: GENERATION},
                    base_values={f: _plain(getattr(record, f)) for f in fields},
                    depends_on=list(depends) if "references_table_id" in fields else [],
                    is_conflict=is_conflict,
                )
            )
        return out

    def _update_table(
        self, spec: GeneratedTable, record: DwTableRecord, targets: Mapping[str, _Target]
    ) -> list[ProposedItem]:
        where = f"{spec.layer}.{spec.name}"
        wanted: dict[str, Any] = {"description": spec.description or ""}
        try:
            wanted["name"] = _identifier(self._platform, spec.name, "name", "table name")
        except ApiError as exc:
            self._problem(where, exc.message)
            return []
        renamed = (record.layer, wanted["name"].lower())
        if wanted["name"].lower() != record.name.lower():
            if renamed in self._renamed or any(
                t.layer == record.layer and t.name.lower() == wanted["name"].lower()
                for t in self._model.tables
            ):
                self._problem(where, "Another table of this Layer has that name.")
            self._renamed.add(renamed)
        if spec.kind == "fact":
            if not spec.grain or not spec.fact_type:
                self._problem(where, "A fact states its grain and fact type.")
            wanted |= {
                "grain": spec.grain,
                "fact_type": spec.fact_type,
                "is_aggregate": spec.is_aggregate,
            }
        if spec.kind == "dimension":
            wanted["scd_type"] = 1 if spec.scd_type is None else spec.scd_type
            target = self._by_id[record.id]
            if not target.keeps_conformed:
                wanted["is_conformed"] = target.derived_conformed
        label = f"{record.layer}.{record.name}"
        plain, conflict = self._split(record, wanted)
        items = self._items(TABLE, record, label, plain, conflict, [])
        existing_columns = self._model.columns[record.id]
        own = {self._model.handle_of[c.id]: c for c in existing_columns if not c.is_system}
        names = {c.name.lower() for c in existing_columns}
        history = (
            wanted.get("scd_type") == 2
            or any(c.scd_type_override == 2 for c in spec.columns)
            or any(c.scd_type_override == 2 for c in existing_columns)
        )
        reserved = self._reserved(record.kind, record.name, history, existing_columns)
        seen: set[uuid.UUID] = set()
        for column in spec.columns:
            col_where = f"{where}.{column.name}"
            existing = own.get(column.handle or "")
            if existing is not None and existing.id not in seen and not existing.is_system:
                seen.add(existing.id)
                items += self._update_column(col_where, spec, record, existing, column, targets)
                continue
            if column.handle:
                self._note(
                    "unknown_handle",
                    col_where,
                    f"{record.name} has no column with the handle {column.handle!r}; it is "
                    "proposed as new.",
                )
            if column.role == "sk" and record.kind in ("dimension", "bridge"):
                continue
            if f"{record.layer}.{record.name}.{column.name}".lower() in self._model.tombstoned:
                self._note(
                    "deleted_by_user",
                    col_where,
                    "A user deleted this generated column, so it is not proposed again.",
                )
                continue
            if column.name.lower() in reserved:
                self._problem(col_where, _reserved_message(column.name))
                continue
            checked = self._column_payload(
                col_where, record.layer, record.kind, record.id, column, targets, new=True
            )
            if checked is None:
                continue
            fields, needs, _ = checked
            flags: list[dict[str, str]] = list(fields.pop("review_flags", []))
            if column.role == "fk" and fields["name"].lower() in names | set(reserved):
                self._problem(col_where, _fk_collision(fields["name"]))
                continue
            if fields["name"].lower() in names:
                flags.append(
                    {
                        "code": "possible_duplicate",
                        "message": f"Possible duplicate of {record.name}.{fields['name']}.",
                    }
                )
                self._note(
                    "possible_duplicate",
                    col_where,
                    "Proposed as new and flagged as a possible duplicate of the column of that "
                    "name (give its handle to change it instead).",
                )
                fields["name"] = _free_name(fields["name"], names, self._limit)
            names_taken = any(f["code"] == "possible_duplicate" for f in flags)
            names.add(fields["name"].lower())
            items.append(
                ProposedItem(
                    key=f"c:{record.id}:{fields['name'].lower()}",
                    object_type=COLUMN,
                    operation="create",
                    label=f"{label}.{fields['name']}"
                    + (" (possible duplicate)" if names_taken else ""),
                    payload={
                        "table_id": str(record.id),
                        **fields,
                        "generation_key": uuid.uuid4().hex,
                        "review_flags": flags,
                        PROVENANCE: GENERATION,
                    },
                    depends_on=[needs] if needs else [],
                )
            )
        audit = self._audit_item(record)
        if audit is not None:
            items.append(audit)
        return items

    def _update_column(
        self,
        where: str,
        spec: GeneratedTable,
        table: DwTableRecord,
        record: DwColumnRecord,
        column: GeneratedColumn,
        targets: Mapping[str, _Target],
    ) -> list[ProposedItem]:
        checked = self._column_payload(
            where, table.layer, table.kind, table.id, column, targets, new=False
        )
        if checked is None:
            return []
        fields, needs, _ = checked
        lookup = fields.pop("lookup", None)
        if lookup is not None:
            self._note(
                "lookup_not_changed",
                where,
                "Lookups are drafted only for new columns; change this one's mapping instead.",
            )
        if any(
            c.id != record.id and c.name.lower() == fields["name"].lower()
            for c in self._model.columns[table.id]
        ):
            self._problem(where, "Another column of this table has that name.")
            return []
        wanted = {f: fields[f] for f in GENERATED_COLUMN_FIELDS}
        plain, conflict = self._split(record, wanted)
        label = f"{table.layer}.{table.name}.{record.name}"
        return self._items(COLUMN, record, label, plain, conflict, [needs] if needs else [])


# -- handlers ---------------------------------------------------------------------------


def _invalid_item(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _warehouse_of(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord:
    warehouse = db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()
    if warehouse is None:
        raise _invalid_item("The Data Warehouse is not set up.")
    return warehouse


def _model_table(
    db: Session, workspace_id: uuid.UUID, object_id: uuid.UUID | None, *, lock: bool = False
) -> DwTableRecord | None:
    if object_id is None:
        return None
    query = (
        sa.select(DwTableRecord)
        .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
        .where(
            DataWarehouseRecord.workspace_id == workspace_id,
            DwTableRecord.layer.in_(MODEL_LAYERS),
            DwTableRecord.status != DELETED,
            DwTableRecord.id == object_id,
        )
    )
    if lock:
        query = query.with_for_update(of=DwTableRecord)
    return db.scalars(query).first()


def _model_column(
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
            DwTableRecord.layer.in_(MODEL_LAYERS),
            DwTableRecord.status != DELETED,
            DwColumnRecord.id == object_id,
        )
    )
    if lock:
        query = query.with_for_update(of=DwColumnRecord)
    row = db.execute(query).first()
    return None if row is None else (row[0], row[1])


_COLUMN_SPEC_KEYS = {*GENERATED_COLUMN_FIELDS, "generation_key", "lookup", "review_flags"}


def _generated_values(column: DwColumnRecord) -> dict[str, Any]:
    snapshot = _column_snapshot(column)
    return {f: snapshot[f] for f in GENERATED_COLUMN_FIELDS}


def _given_fields(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k in GENERATED_COLUMN_FIELDS}


def _apply_lookup(
    db: Session,
    warehouse: DataWarehouseRecord,
    table: DwTableRecord,
    column: DwColumnRecord,
    lookup: Mapping[str, Any],
    at: datetime,
) -> None:
    """Map a new Core fact FK as a lookup (table level), with its lineage edges. A lookup
    that no longer fits (a staging column gone) is left out and flagged on the column."""
    mappings = MappingService(None, workspaces=None, clock=lambda: at)  # type: ignore[arg-type]
    try:
        spec, parsed, dimension = mappings._lookup(db, warehouse, table, column, lookup)
        inputs = mappings._resolve(db, warehouse, table, parsed)
    except ApiError as exc:
        column.review_flags = [
            *column.review_flags,
            {"code": "lookup_not_applied", "message": exc.message},
        ]
        return
    table_mapping = mappings._table_mapping(db, table, at)
    record = ColumnMappingRecord(
        id=uuid.uuid4(),
        table_mapping_id=table_mapping.id,
        branch_id=None,
        dw_column_id=column.id,
        mapping_type="lookup",
        rule_text="",
        sql_expression="",
        lookup=spec,
        validation={"unparsed": False, "errors": []},
        updated_by=None,
        updated_at=at,
        version=1,
    )
    db.add(record)
    db.flush()
    mappings._replace_edges(db, record, table, parsed, inputs, dimension)


def _take_generated(
    record: DwTableRecord | DwColumnRecord,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    changed: Sequence[str],
    fields: tuple[str, ...],
    payload: Mapping[str, Any],
    scope: Mapping[str, Any] | None,
) -> None:
    """Provenance after an accepted update: the generator's value is the last generated one
    and no longer an override; anyone else's change to a generated object is a user edit,
    including an item of any other Change Set (e.g. one the assistant proposed in a chat
    with ``propose_changes``), whatever its payload claims."""
    generating = (scope or {}).get("kind") == SCOPE["kind"]
    if payload.get(PROVENANCE) != GENERATION or not generating:
        mark_edited(record, before, after, fields)
        return
    if record.generation_key is None:
        return
    record.generated = {**(record.generated or {}), **{f: after[f] for f in changed}}
    record.edited_fields = [f for f in record.edited_fields if f not in changed]


def _check_provenance(payload: Mapping[str, Any]) -> None:
    """What a create carries besides its fields must fit: any item may come from a model."""
    key = payload.get("generation_key")
    if key is not None and (not isinstance(key, str) or not 0 < len(key) <= 64):
        raise _invalid_item("A generation key is a short text.")
    flags = payload.get("review_flags", [])
    if not isinstance(flags, list) or not all(
        isinstance(f, Mapping)
        and set(f) == {"code", "message"}
        and all(isinstance(v, str) and len(v) <= 500 for v in f.values())
        for f in flags
    ):
        raise _invalid_item("Review flags are a list of {code, message}.")


class _Handler:
    object_type: str
    wants_scope = True
    """Only an item of a generation Change Set (``SCOPE``) applies as the generator's."""

    def required_action(self, operation: str) -> Action:
        if operation in ("create", "update"):
            return Action.EDIT_DW_SCHEMA
        raise ValueError(f"A {self.object_type} item cannot {operation}.")


class DwTableHandler(_Handler):
    """Creates (with nested columns, or the built-in date dimension) and updates of Core
    and Mart tables."""

    object_type = TABLE
    _UPDATABLE = (*GENERATED_TABLE_FIELDS, AUDIT_COLUMNS, PROVENANCE)

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if operation == "create":
            if _uuid(payload.get("id")) is None or payload.get("layer") not in MODEL_LAYERS:
                raise _invalid_item("A table create carries its id and a Core or Mart layer.")
            _check_provenance(payload)
            for column in payload.get("columns") or []:
                if isinstance(column, Mapping):
                    _check_provenance(column)
            if payload.get("builtin") is not None:
                if payload["builtin"] != "date":
                    raise _invalid_item("The built-in table is the date dimension.")
                return
            columns = payload.get("columns")
            if (
                payload.get("kind") not in MODEL_KINDS
                or not isinstance(payload.get("name"), str)
                or not isinstance(columns, list)
                or not all(
                    isinstance(c, Mapping)
                    and {"name", "data_type", "role"} <= set(c)
                    and set(c) <= _COLUMN_SPEC_KEYS
                    for c in columns
                )
            ):
                raise _invalid_item("A table create names its kind, name and columns.")
            return
        if not payload or any(f not in self._UPDATABLE for f in payload):
            raise _invalid_item(
                f"A table item changes some of {', '.join(GENERATED_TABLE_FIELDS)}."
            )
        if _model_table(db, workspace_id, object_id) is None:
            raise _invalid_item("The table does not exist.")

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        table = _model_table(db, workspace_id, object_id, lock=True)
        if table is None:
            return None
        return {f: _plain(getattr(table, f, None)) for f in fields}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
        scope: Mapping[str, Any] | None = None,
    ) -> AppliedChange:
        warehouse = _warehouse_of(db, workspace_id)
        rules = editing_rules(lambda: at)
        if operation == "create":
            return self._create(db, workspace_id, warehouse, payload, at)
        table = _model_table(db, workspace_id, object_id, lock=True)
        if table is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The table no longer exists.")
        columns = rules._columns(db, table.id)
        before = _table_snapshot(table)
        changes = {f: v for f, v in payload.items() if f in GENERATED_TABLE_FIELDS}
        rules._apply_table(db, warehouse, table, columns, changes)
        db.flush()
        after = _table_snapshot(table)
        changed = [f for f in GENERATED_TABLE_FIELDS if before[f] != after[f]]
        _take_generated(table, before, after, changed, GENERATED_TABLE_FIELDS, payload, scope)
        rules._sync_scd2(db, None, workspace_id, table, at)
        added = (
            rules._sync_audit(db, None, workspace_id, warehouse, table, at)
            if payload.get(AUDIT_COLUMNS)
            else []
        )
        if changed or added:
            table.version += 1
            table.updated_at = at
        db.flush()
        old: dict[str, Any] = {f: before[f] for f in changed}
        new: dict[str, Any] = {f: after[f] for f in changed}
        if added:
            old[AUDIT_COLUMNS], new[AUDIT_COLUMNS] = [], added
        return AppliedChange(TABLE, table.id, old, new, f"{table.layer}.{table.name}")

    def _create(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        warehouse: DataWarehouseRecord,
        payload: Mapping[str, Any],
        at: datetime,
    ) -> AppliedChange:
        rules = editing_rules(lambda: at)
        table_id = uuid.UUID(payload["id"])
        if payload.get("builtin"):
            table = rules._build_generated(
                db, None, workspace_id, warehouse, "date", payload["layer"], at, table_id=table_id
            )
            table.generation_key = payload.get("generation_key")
            table.generated = {}
            db.flush()
            return AppliedChange(
                TABLE, table.id, None, {"name": table.name, "kind": "generated"}, table.name
            )
        table = rules._build_table(
            db,
            warehouse,
            None,
            {
                f: payload.get(f)
                for f in (
                    "layer",
                    "name",
                    "kind",
                    "grain",
                    "fact_type",
                    "is_aggregate",
                    "scd_type",
                    "is_conformed",
                    "description",
                )
                if payload.get(f) is not None
            },
            at,
        )
        table.id = table_id
        table.review_flags = list(payload.get("review_flags") or [])
        table.generation_key = payload.get("generation_key")
        db.add(table)
        db.flush()
        rules._scaffold_key(db, None, workspace_id, warehouse, table, at)
        lookups: list[tuple[DwColumnRecord, Mapping[str, Any]]] = []
        for spec in payload["columns"]:
            try:
                column = rules._add_column(
                    db, None, workspace_id, table, _given_fields(spec), at, scaffold=True
                )
            except ApiError as exc:  # name the column the table's create failed on
                raise ApiError(
                    exc.status_code,
                    exc.code,
                    exc.message,
                    {**exc.details, "column": spec.get("name")},
                ) from None
            column.generation_key = spec.get("generation_key")
            column.generated = _generated_values(column)
            column.review_flags = list(spec.get("review_flags") or [])
            if spec.get("lookup"):
                lookups.append((column, spec["lookup"]))
        rules._sync_scd2(db, None, workspace_id, table, at)
        rules._sync_audit(db, None, workspace_id, warehouse, table, at)  # every table has them
        table.generated = {f: _plain(getattr(table, f)) for f in GENERATED_TABLE_FIELDS}
        db.flush()
        for column, lookup in lookups:
            _apply_lookup(db, warehouse, table, column, lookup, at)
        return AppliedChange(
            TABLE,
            table.id,
            None,
            {
                "layer": table.layer,
                "name": table.name,
                "kind": table.kind,
                "columns": len(payload["columns"]),
            },
            f"{table.layer}.{table.name}",
        )


class DwColumnHandler(_Handler):
    """Creates and updates of columns of existing Core and Mart tables."""

    object_type = COLUMN
    _UPDATABLE = (*GENERATED_COLUMN_FIELDS, PROVENANCE)

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        if operation == "create":
            _check_provenance(payload)
            table = _model_table(db, workspace_id, _uuid(payload.get("table_id")))
            if table is None or not {"name", "data_type", "role"} <= set(payload):
                raise _invalid_item("A column create names its table, name, type and role.")
            return
        if not payload or any(f not in self._UPDATABLE for f in payload):
            raise _invalid_item(
                f"A column item changes some of {', '.join(GENERATED_COLUMN_FIELDS)}."
            )
        found = _model_column(db, workspace_id, object_id)
        if found is None or found[0].is_system:
            raise _invalid_item("The column does not exist or DAWAM maintains it.")

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        found = _model_column(db, workspace_id, object_id, lock=True)
        if found is None:
            return None
        return {f: _plain(getattr(found[0], f, None)) for f in fields}

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
        scope: Mapping[str, Any] | None = None,
    ) -> AppliedChange:
        warehouse = _warehouse_of(db, workspace_id)
        rules = editing_rules(lambda: at)
        if operation == "create":
            table = _model_table(db, workspace_id, _uuid(payload["table_id"]), lock=True)
            if table is None:
                raise ApiError(409, "version_conflict", "The table no longer exists.")
            column = rules._add_column(
                db, None, workspace_id, table, _given_fields(payload), at, scaffold=True
            )
            column.generation_key = payload.get("generation_key")
            column.generated = _generated_values(column)
            column.review_flags = list(payload.get("review_flags") or [])
            rules._sync_scd2(db, None, workspace_id, table, at)
            table.version += 1
            table.updated_at = at
            db.flush()
            if payload.get("lookup"):
                _apply_lookup(db, warehouse, table, column, payload["lookup"], at)
            return AppliedChange(
                COLUMN,
                column.id,
                None,
                {"name": column.name, "role": column.role},
                f"{table.layer}.{table.name}.{column.name}",
            )
        found = _model_column(db, workspace_id, object_id, lock=True)
        if found is None or found[0].is_system:  # pragma: no cover - checked under the lock
            raise ApiError(409, "version_conflict", "The column no longer exists.")
        column, table = found
        before = _column_snapshot(column)
        siblings = [c for c in rules._columns(db, table.id) if c.id != column.id]
        changes = {f: v for f, v in payload.items() if f in GENERATED_COLUMN_FIELDS}
        final = {**before, **rules._column_fields(warehouse, changes, partial=True)}
        final["references_table_id"] = _uuid(final["references_table_id"])
        rules._check_column(db, warehouse, table, siblings, final)
        for name in GENERATED_COLUMN_FIELDS:
            setattr(column, name, final[name])
        after = _column_snapshot(column)
        changed = [f for f in GENERATED_COLUMN_FIELDS if before[f] != after[f]]
        if "name" in changed:
            rules._rename_default(table, before["name"], column.name)
        _take_generated(column, before, after, changed, GENERATED_COLUMN_FIELDS, payload, scope)
        if changed:
            column.version += 1
            column.updated_at = at
        rules._sync_scd2(db, None, workspace_id, table, at)
        db.flush()
        return AppliedChange(
            COLUMN,
            column.id,
            {f: before[f] for f in changed},
            {f: after[f] for f in changed},
            f"{table.layer}.{table.name}.{column.name}",
        )
