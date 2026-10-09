"""Building the rows of Staging Tables (spec §6.7), shared by generation and sync.

Pure with respect to the database: ``build_staging`` turns Source Tables into the rows of
their Staging Tables, mappings and lineage edges; ``write_rows`` inserts them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.sources import StagingSystem, StagingTable

from .platforms import SAFE_IDENTIFIER, TargetPlatform
from .staging_naming import Flag, NameInput, column_names, table_names
from .staging_types import translate_type
from .tables import (
    NAME_MAX_LENGTH,
    ColumnMappingRecord,
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


def _dict(flags: list[Flag] | tuple[Flag, ...]) -> list[dict[str, str]]:
    return [{"code": f.code, "message": f.message} for f in flags]


def _quote(part: str) -> str:
    return part if SAFE_IDENTIFIER.fullmatch(part) else '"' + part.replace('"', '""') + '"'


def build_staging(
    actor_id: uuid.UUID | None,
    warehouse_id: uuid.UUID,
    platform: TargetPlatform,
    audit_names: tuple[str, str],
    systems: list[StagingSystem],
    taken: set[str],
    now: datetime,
) -> tuple[_Rows, list[StagingFlag]]:
    """The rows (not yet written) of a Staging Table for every table of ``systems``, named
    among themselves and the ``taken`` names."""
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
            _stage_table(
                rows,
                flags,
                actor_id,
                warehouse_id,
                platform,
                audit_names,
                system,
                table,
                names[str(table.id)],
                now,
            )
    return rows, flags


def write_rows(db: Session, rows: _Rows) -> None:
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


def _stage_table(
    rows: _Rows,
    flags: list[StagingFlag],
    actor_id: uuid.UUID | None,
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
            "created_by": actor_id,
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
                "updated_by": actor_id,
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
