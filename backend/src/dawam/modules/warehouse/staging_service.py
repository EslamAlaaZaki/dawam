"""Staging generation (spec §6.7, stories 81-85).

Once the Data Warehouse is set up, DAWAM makes the Staging Layer by software, with no AI: one
Staging Table per source base table of every Source System (a view only when an editor opted
it in), named ``stg_<system code>_<database schema>_<table>``, with the source's columns in
the target platform's types plus the audit columns, and a ``direct`` mapping (and lineage
edge) from every source column to its staging column.

Generation only adds: a Source Table that already has a Staging Table is left alone (syncing
changes of a later Snapshot is a Change Set, not this). Every method authorizes through the
workspaces policy first. The batch is audited and recorded in the activity feed once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.sources import StagingSourceService, StagingSystem, StagingTable
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .platforms import SAFE_IDENTIFIER, TargetPlatform
from .service import NamingRules
from .staging_naming import Flag, NameInput, column_names, needs_placeholder, table_names
from .staging_types import translate_type
from .tables import (
    NAME_MAX_LENGTH,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LineageEdgeRecord,
    TableMappingRecord,
)

_NEW_VALIDATION: dict[str, Any] = {"unparsed": False, "errors": []}
_SOURCE_SYSTEM_LENGTH = 24


@dataclass(frozen=True)
class StagingFlag:
    """One thing generation flagged for review."""

    table_id: uuid.UUID
    table_name: str
    column_name: str | None
    """``None`` for a flag on the table itself."""
    code: str
    message: str


@dataclass(frozen=True)
class StagingResult:
    tables_created: int
    columns_created: int
    tables_existing: int
    """Source tables that already had a Staging Table and were left as they are."""
    flags: list[StagingFlag]


def _dict(flags: list[Flag] | tuple[Flag, ...]) -> list[dict[str, str]]:
    return [{"code": f.code, "message": f.message} for f in flags]


def _quote(part: str) -> str:
    return part if SAFE_IDENTIFIER.fullmatch(part) else '"' + part.replace('"', '""') + '"'


class StagingService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        sources: StagingSourceService | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._sources = sources or StagingSourceService(engine)

    def generate(self, user: User, workspace_id: uuid.UUID) -> StagingResult:
        """Generate the Staging Tables not yet made (owners and editors). 404 ``not_set_up``
        before the Data Warehouse exists."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        with Session(self._engine) as db:
            warehouse = db.scalars(
                sa.select(DataWarehouseRecord).where(
                    DataWarehouseRecord.workspace_id == workspace_id
                )
            ).first()
            if warehouse is None:
                raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
            staged = {
                source_id
                for (source_id,) in db.execute(
                    sa.select(DwTableRecord.source_table_id).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                        DwTableRecord.source_table_id.is_not(None),
                    )
                )
            }
            taken = set(
                db.scalars(
                    sa.select(sa.func.lower(DwTableRecord.name)).where(
                        DwTableRecord.data_warehouse_id == warehouse.id,
                        DwTableRecord.layer == "staging",
                    )
                )
            )
            platform: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
            rules = NamingRules(**warehouse.naming_rules)
            warehouse_id = warehouse.id
        systems, existing = self._read(workspace_id, staged)
        try:
            with Session(self._engine) as db, db.begin():
                result = self._insert(
                    db,
                    user,
                    workspace_id,
                    warehouse_id,
                    platform,
                    (rules.load_ts_column, rules.source_system_column),
                    systems,
                    taken,
                    existing,
                )
        except IntegrityError:
            raise ApiError(
                409,
                "conflict",
                "Staging was generated at the same time by someone else. Try again.",
            ) from None
        return result

    def _read(
        self, workspace_id: uuid.UUID, staged: set[uuid.UUID]
    ) -> tuple[list[StagingSystem], int]:
        """The tables still to stage (with placeholder numbers given to their non-Latin
        names) and how many source tables already had a Staging Table."""
        found = self._sources.read(workspace_id)
        existing = sum(1 for s in found for t in s.tables if t.id in staged)
        systems = self._pending(found, staged)
        tables = [
            t.id
            for s in systems
            for t in s.tables
            if t.placeholder_no is None and needs_placeholder(t.name)
        ]
        columns = [
            c.id
            for s in systems
            for t in s.tables
            for c in t.columns
            if c.placeholder_no is None and needs_placeholder(c.name)
        ]
        if tables or columns:
            self._sources.assign_placeholders(workspace_id, tables=tables, columns=columns)
            systems = self._pending(self._sources.read(workspace_id), staged)
        return systems, existing

    @staticmethod
    def _pending(systems: list[StagingSystem], staged: set[uuid.UUID]) -> list[StagingSystem]:
        return [
            StagingSystem(s.id, s.code, s.engine, [t for t in s.tables if t.id not in staged])
            for s in systems
        ]

    def _insert(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        platform: TargetPlatform,
        audit_names: tuple[str, str],
        systems: list[StagingSystem],
        taken: set[str],
        existing: int,
    ) -> StagingResult:
        now = self._clock()
        names = table_names(
            platform,
            tables=[
                (
                    system.code,
                    NameInput(str(table.db_schema_id), table.db_schema),
                    NameInput(str(table.id), table.name, table.placeholder_no),
                )
                for system in systems
                for table in system.tables
            ],
            taken=taken,
            max_length=NAME_MAX_LENGTH,
        )
        rows = _Rows()
        flags: list[StagingFlag] = []
        for system in systems:
            for table in system.tables:
                named = names[str(table.id)]
                self._stage_table(
                    rows,
                    flags,
                    user,
                    warehouse_id,
                    platform,
                    audit_names,
                    system,
                    table,
                    named,
                    now,
                )
        for model, items in (
            (DwTableRecord, rows.tables),
            (TableMappingRecord, rows.table_mappings),
            (DwColumnRecord, rows.columns),
            (ColumnMappingRecord, rows.column_mappings),
            (LineageEdgeRecord, rows.edges),
        ):
            if not items:
                continue
            # One multi-row INSERT per chunk (a round trip per row is what made it slow),
            # kept under the driver's 65 535 bind parameters.
            step = max(1, 30_000 // len(items[0]))
            for start in range(0, len(items), step):
                db.execute(sa.insert(model.__table__).values(items[start : start + step]))
        if rows.tables:
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="data_warehouse",
                entity_id=warehouse_id,
                old=None,
                new={
                    "staging_generated": {
                        "tables": len(rows.tables),
                        "columns": len(rows.columns),
                        "flagged": len(flags),
                    }
                },
                at=now,
            )
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="staging.generated",
                object_type="data_warehouse",
                object_id=warehouse_id,
                object_label="Staging Layer",
                details={"tables": len(rows.tables), "columns": len(rows.columns)},
                at=now,
            )
        return StagingResult(len(rows.tables), len(rows.columns), existing, flags)

    def _stage_table(
        self,
        rows: _Rows,
        flags: list[StagingFlag],
        user: User,
        warehouse_id: uuid.UUID,
        platform: TargetPlatform,
        audit_names: tuple[str, str],
        system: StagingSystem,
        table: StagingTable,
        named: Any,
        now: datetime,
    ) -> None:
        table_id = uuid.uuid4()
        rows.tables.append(
            {
                "id": table_id,
                "data_warehouse_id": warehouse_id,
                "layer": "staging",
                "name": named.name,
                "kind": "staging",
                "is_aggregate": False,
                "is_conformed": False,
                "description": "",
                "source_table_id": table.id,
                "review_flags": _dict(named.flags),
                "created_by": user.id,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
        )
        mapping_id = uuid.uuid4()
        rows.table_mappings.append(
            {
                "id": mapping_id,
                "dw_table_id": table_id,
                "match_keys": [],
                "notes": "",
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
        )
        for flag in named.flags:
            flags.append(StagingFlag(table_id, named.name, None, flag.code, flag.message))
        load_ts, source_system = audit_names
        column_named = column_names(
            platform,
            columns=[NameInput(str(c.id), c.name, c.placeholder_no) for c in table.columns],
            taken={load_ts, source_system},
            max_length=NAME_MAX_LENGTH,
        )
        source_prefix = ".".join(_quote(p) for p in (table.db_schema, table.name))
        ordinal = 0

        def add_column(
            name: str,
            data_type: dict[str, Any],
            nullable: bool,
            *,
            role: str,
            source_column_id: uuid.UUID | None,
            review: list[dict[str, str]],
            mapping_type: str,
            sql: str,
        ) -> uuid.UUID:
            nonlocal ordinal
            ordinal += 1
            column_id = uuid.uuid4()
            rows.columns.append(
                {
                    "id": column_id,
                    "table_id": table_id,
                    "name": name,
                    "ordinal": ordinal,
                    "data_type": data_type,
                    "is_nullable": nullable,
                    "role": role,
                    "description": "",
                    "is_system": role == "audit",
                    "source_column_id": source_column_id,
                    "review_flags": review,
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                }
            )
            rows.column_mappings.append(
                {
                    "id": (mapping := uuid.uuid4()),
                    "table_mapping_id": mapping_id,
                    "dw_column_id": column_id,
                    "mapping_type": mapping_type,
                    "rule_text": "",
                    "sql_expression": sql,
                    "validation": _NEW_VALIDATION,
                    "updated_by": user.id,
                    "updated_at": now,
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
                        "mapping_id": mapping,
                    }
                )
            return column_id

        for column in table.columns:
            translated = translate_type(platform, column.data_type, engine=system.engine)
            column_name = column_named[str(column.id)]
            all_flags = [*column_name.flags, *translated.flags]
            add_column(
                column_name.name,
                translated.data_type,
                column.is_nullable,
                role="attribute",
                source_column_id=column.id,
                review=_dict(all_flags),
                mapping_type="direct",
                sql=f"{source_prefix}.{_quote(column.name)}",
            )
            for flag in all_flags:
                flags.append(
                    StagingFlag(table_id, named.name, column_name.name, flag.code, flag.message)
                )
        add_column(
            load_ts,
            {"type": "timestamp", "length": None, "precision": None, "scale": None},
            False,
            role="audit",
            source_column_id=None,
            review=[],
            mapping_type="system",
            sql="CURRENT_TIMESTAMP",
        )
        add_column(
            source_system,
            {"type": "string", "length": _SOURCE_SYSTEM_LENGTH, "precision": None, "scale": None},
            False,
            role="audit",
            source_column_id=None,
            review=[],
            mapping_type="system",
            sql="'" + system.code.replace("'", "''") + "'",
        )


@dataclass
class _Rows:
    tables: list[dict[str, Any]] = field(default_factory=list)
    table_mappings: list[dict[str, Any]] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)
    column_mappings: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
