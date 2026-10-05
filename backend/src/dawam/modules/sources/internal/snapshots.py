"""Storing an extracted catalog as a Snapshot (spec §6.3, §7 "Source identity vs Snapshots").

``store_catalog`` matches what was extracted to the Source System's stable Source Objects
(``SrcDbSchema``, ``SrcTable``, ``SrcColumn``, ``SrcRoutine``), creating the new ones, and
writes the immutable ``Snapshot*`` rows that point at them:

- objects are matched by exact name first; when the engine folds case, a name that
  differs only in letter case matches too, but only when that is unambiguous (so
  PostgreSQL's ``"Customer"`` and ``customer`` stay two objects);
- a changed definition (a type change, ...) updates the identity and bumps its version;
- objects missing from the catalog become ``source_removed``, or ``out_of_scope`` when
  their Database Schema is no longer allowed; nothing is ever deleted, and a ``deleted``
  object (a user's soft delete) keeps that state;
- a catalog identical to the latest Snapshot's (same content hash) creates nothing;
- view and routine text is stored once per content hash (``definition_texts``).

The caller holds the Source System's row lock, so extractions of one system never
interleave.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable, Hashable, Iterable, Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..tables import (
    DefinitionTextRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotDbSchemaRecord,
    SnapshotIndexRecord,
    SnapshotRecord,
    SnapshotRoutineRecord,
    SnapshotTableRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcRoutineRecord,
    SrcTableRecord,
)
from .connector import SourceCatalog

_INSERT_BATCH = 5000


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(catalog: SourceCatalog) -> str:
    """A fingerprint of everything a Snapshot captures, independent of row order."""
    content = {
        "schemas": sorted(catalog.schemas),
        "tables": sorted(
            (dataclasses.asdict(t) for t in catalog.tables), key=lambda t: (t["schema"], t["name"])
        ),
        "routines": sorted(
            (dataclasses.asdict(r) for r in catalog.routines),
            key=lambda r: (r["schema"], r["name"], r["kind"], r["signature"]),
        ),
    }
    return text_hash(json.dumps(content, sort_keys=True, ensure_ascii=False))


def _match[K: Hashable, R](
    existing: Mapping[K, R], incoming: Iterable[K], fold: Callable[[K], Hashable] | None
) -> dict[K, R]:
    """Pair incoming keys with existing identities: exact key first, then (with ``fold``)
    a folded key, only where exactly one incoming and one unmatched existing share it."""
    wanted = list(incoming)
    matched = {key: existing[key] for key in wanted if key in existing}
    if fold is None:
        return matched
    taken = {id(record) for record in matched.values()}
    spare: dict[Hashable, list[R]] = {}
    for key, record in existing.items():
        if id(record) not in taken:
            spare.setdefault(fold(key), []).append(record)
    unmatched: dict[Hashable, list[K]] = {}
    for key in wanted:
        if key not in matched:
            unmatched.setdefault(fold(key), []).append(key)
    for folded, keys in unmatched.items():
        candidates = spare.get(folded, [])
        if len(keys) == 1 and len(candidates) == 1:
            matched[keys[0]] = candidates[0]
    return matched


def _bring_back(record: Any) -> None:
    if record.status != "deleted":
        record.status = "present"


def _set_missing(record: Any, status: str) -> None:
    if record.status != "deleted":
        record.status = status


def _revise(record: SrcTableRecord | SrcColumnRecord, name: str, definition: dict) -> None:
    """Point an identity at its latest name and definition, bumping its version on change."""
    if record.name != name or record.current_definition != definition:
        record.name = name
        record.current_definition = definition
        record.version += 1


def _insert(db: Session, record_class: type, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), _INSERT_BATCH):
        db.execute(sa.insert(record_class), rows[start : start + _INSERT_BATCH])


def store_catalog(
    db: Session,
    *,
    source_system_id: uuid.UUID,
    catalog: SourceCatalog,
    allowed_schemas: Iterable[str],
    origin: str,
    job_id: uuid.UUID | None,
    taken_at: datetime,
    folds_case: bool,
) -> SnapshotRecord | None:
    """Store ``catalog`` as the Source System's new latest Snapshot and return it; ``None``
    (and nothing written) if it does not differ from the latest one."""
    digest = content_hash(catalog)
    latest = db.scalars(
        sa.select(SnapshotRecord).where(
            SnapshotRecord.source_system_id == source_system_id, SnapshotRecord.is_latest
        )
    ).first()
    if latest is not None and latest.content_hash == digest:
        return None
    allowed = set(allowed_schemas)
    name_fold: Callable[[str], Hashable] | None = str.casefold if folds_case else None

    def keyed_fold(position: int) -> Callable[[tuple], Hashable] | None:
        if not folds_case:
            return None
        return lambda key: (*key[:position], key[position].casefold(), *key[position + 1 :])

    # -- Database Schemas ---------------------------------------------------------------
    existing_schemas = {
        record.name: record
        for record in db.scalars(
            sa.select(SrcDbSchemaRecord).where(
                SrcDbSchemaRecord.source_system_id == source_system_id
            )
        )
    }
    schema_names = sorted(
        set(catalog.schemas)
        | {t.schema for t in catalog.tables}
        | {r.schema for r in catalog.routines}
    )
    matched_schemas = _match(existing_schemas, schema_names, name_fold)
    schemas: dict[str, SrcDbSchemaRecord] = {}
    for name in schema_names:
        schema = matched_schemas.get(name)
        if schema is None:
            schema = SrcDbSchemaRecord(
                id=uuid.uuid4(), source_system_id=source_system_id, name=name, status="present"
            )
            db.add(schema)
        else:
            schema.name = name
            _bring_back(schema)
        schemas[name] = schema
    schema_by_id = {s.id: s for s in [*existing_schemas.values(), *schemas.values()]}
    seen = {id(s) for s in schemas.values()}

    def missing_status(schema: SrcDbSchemaRecord) -> str:
        return "source_removed" if schema.name in allowed else "out_of_scope"

    for schema in existing_schemas.values():
        if id(schema) not in seen:
            _set_missing(schema, missing_status(schema))

    # -- Tables and views ---------------------------------------------------------------
    hashes: dict[str, str] = {}

    def keep_text(text: str | None) -> str | None:
        if text is None:
            return None
        digest = text_hash(text)
        hashes[digest] = text
        return digest

    existing_tables = {
        (record.db_schema_id, record.name): record
        for record in db.scalars(
            sa.select(SrcTableRecord)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(SrcDbSchemaRecord.source_system_id == source_system_id)
        )
    }
    incoming_tables = {(schemas[t.schema].id, t.name): t for t in catalog.tables}
    matched_tables = _match(existing_tables, incoming_tables, keyed_fold(1))
    tables: dict[tuple[str, str], SrcTableRecord] = {}
    view_hashes: dict[tuple[str, str], str | None] = {}
    for key, info in incoming_tables.items():
        view_hash = keep_text(info.definition) if info.kind == "view" else None
        view_hashes[(info.schema, info.name)] = view_hash
        definition = {"kind": info.kind, "comment": info.comment, "view_definition_hash": view_hash}
        table = matched_tables.get(key)
        if table is None:
            table = SrcTableRecord(
                id=uuid.uuid4(),
                db_schema_id=key[0],
                name=info.name,
                kind=info.kind,
                current_definition=definition,
                status="present",
                version=1,
            )
            db.add(table)
        else:
            table.kind = info.kind
            _revise(table, info.name, definition)
            _bring_back(table)
        tables[(info.schema, info.name)] = table
    present_tables = {id(t) for t in tables.values()}
    table_by_id = {t.id: t for t in existing_tables.values()}
    for table in existing_tables.values():
        if id(table) not in present_tables:
            _set_missing(table, missing_status(schema_by_id[table.db_schema_id]))

    # -- Columns ------------------------------------------------------------------------
    existing_columns = {
        (record.table_id, record.name): record
        for record in db.scalars(
            sa.select(SrcColumnRecord)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(SrcDbSchemaRecord.source_system_id == source_system_id)
        )
    }
    incoming_columns = {
        (tables[(t.schema, t.name)].id, c.name): c for t in catalog.tables for c in t.columns
    }
    matched_columns = _match(existing_columns, incoming_columns, keyed_fold(1))
    columns: dict[tuple[uuid.UUID, str], SrcColumnRecord] = {}
    for key, info in incoming_columns.items():
        definition = {
            "data_type": info.data_type,
            "is_nullable": info.is_nullable,
            "is_pk": info.is_pk,
            "default": info.default,
            "comment": info.comment,
        }
        column = matched_columns.get(key)
        if column is None:
            column = SrcColumnRecord(
                id=uuid.uuid4(),
                table_id=key[0],
                name=info.name,
                current_definition=definition,
                status="present",
                version=1,
            )
            db.add(column)
        else:
            _revise(column, info.name, definition)
            _bring_back(column)
        columns[(key[0], info.name)] = column
    present_columns = {id(c) for c in columns.values()}
    for column in existing_columns.values():
        if id(column) in present_columns:
            continue
        table = table_by_id.get(column.table_id)
        if table is not None and id(table) in present_tables:
            _set_missing(column, "source_removed")
        elif table is not None:
            _set_missing(column, missing_status(schema_by_id[table.db_schema_id]))

    # -- Routines -----------------------------------------------------------------------
    existing_routines = {
        (record.db_schema_id, record.kind, record.signature, record.name): record
        for record in db.scalars(
            sa.select(SrcRoutineRecord)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcRoutineRecord.db_schema_id)
            .where(SrcDbSchemaRecord.source_system_id == source_system_id)
        )
    }
    incoming_routines = {
        (schemas[r.schema].id, r.kind, r.signature, r.name): r for r in catalog.routines
    }
    matched_routines = _match(existing_routines, incoming_routines, keyed_fold(3))
    routines: list[tuple[SrcRoutineRecord, Any]] = []
    for key, info in incoming_routines.items():
        routine = matched_routines.get(key)
        if routine is None:
            routine = SrcRoutineRecord(
                id=uuid.uuid4(),
                db_schema_id=key[0],
                name=info.name,
                kind=info.kind,
                signature=info.signature,
                status="present",
            )
            db.add(routine)
        else:
            routine.name = info.name
            _bring_back(routine)
        routines.append((routine, info))
    present_routines = {id(r) for r, _ in routines}
    for routine in existing_routines.values():
        if id(routine) not in present_routines:
            _set_missing(routine, missing_status(schema_by_id[routine.db_schema_id]))

    # -- The Snapshot -------------------------------------------------------------------
    routine_hashes = [keep_text(info.definition) for _, info in routines]
    if hashes:
        db.execute(
            pg_insert(DefinitionTextRecord)
            .values([{"hash": h, "text": text} for h, text in hashes.items()])
            .on_conflict_do_nothing(index_elements=["hash"])
        )
    if latest is not None:
        latest.is_latest = False
    db.flush()
    snapshot = SnapshotRecord(
        id=uuid.uuid4(),
        source_system_id=source_system_id,
        origin=origin,
        job_id=job_id,
        taken_at=taken_at,
        is_latest=True,
        content_hash=digest,
        schema_count=len(schemas),
        table_count=len(catalog.tables),
        column_count=len(incoming_columns),
        routine_count=len(routines),
    )
    db.add(snapshot)
    db.flush()
    sid = snapshot.id
    _insert(
        db,
        SnapshotDbSchemaRecord,
        [{"snapshot_id": sid, "src_db_schema_id": s.id, "name": n} for n, s in schemas.items()],
    )
    table_rows: list[dict[str, Any]] = []
    column_rows: list[dict[str, Any]] = []
    constraint_rows: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []
    for info in catalog.tables:
        table = tables[(info.schema, info.name)]
        table_rows.append(
            {
                "snapshot_id": sid,
                "src_table_id": table.id,
                "db_schema": info.schema,
                "name": info.name,
                "kind": info.kind,
                "view_definition_hash": view_hashes[(info.schema, info.name)],
                "row_estimate": info.row_estimate,
                "comment": info.comment,
            }
        )
        for c in info.columns:
            column_rows.append(
                {
                    "snapshot_id": sid,
                    "src_column_id": columns[(table.id, c.name)].id,
                    "src_table_id": table.id,
                    "name": c.name,
                    "ordinal": c.ordinal,
                    "data_type": c.data_type,
                    "is_nullable": c.is_nullable,
                    "is_pk": c.is_pk,
                    "default": c.default,
                    "comment": c.comment,
                }
            )
        named: set[str] = set()
        for constraint in info.constraints:
            if constraint.name in named:
                continue
            named.add(constraint.name)
            referenced = tables.get((constraint.ref_schema or "", constraint.ref_table or ""))
            constraint_rows.append(
                {
                    "snapshot_id": sid,
                    "src_table_id": table.id,
                    "name": constraint.name,
                    "type": constraint.type,
                    "columns": list(constraint.columns),
                    "ref_table_id": referenced.id if referenced is not None else None,
                    "ref_db_schema": constraint.ref_schema,
                    "ref_table": constraint.ref_table,
                    "ref_columns": list(constraint.ref_columns),
                }
            )
        indexed: set[str] = set()
        for index in info.indexes:
            if index.name in indexed:
                continue
            indexed.add(index.name)
            index_rows.append(
                {
                    "snapshot_id": sid,
                    "src_table_id": table.id,
                    "name": index.name,
                    "columns": list(index.columns),
                    "is_unique": index.is_unique,
                }
            )
    _insert(db, SnapshotTableRecord, table_rows)
    _insert(db, SnapshotColumnRecord, column_rows)
    _insert(db, SnapshotConstraintRecord, constraint_rows)
    _insert(db, SnapshotIndexRecord, index_rows)
    _insert(
        db,
        SnapshotRoutineRecord,
        [
            {
                "snapshot_id": sid,
                "src_routine_id": routine.id,
                "db_schema": info.schema,
                "name": info.name,
                "kind": info.kind,
                "signature": info.signature,
                "definition_hash": definition_hash,
            }
            for (routine, info), definition_hash in zip(routines, routine_hashes, strict=True)
        ],
    )
    return snapshot
