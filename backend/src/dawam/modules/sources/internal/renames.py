"""Renamed Source Objects (spec story 52a, §7 "Source identity vs Snapshots").

A column, table or Database Schema that was renamed looks, to the matcher, like one object
removed and another added. This module does two things about it:

- ``column_matches``, ``table_matches`` and ``schema_matches`` pair removed objects with
  added ones that look alike, each with a confidence between 0 and 1. They are pure: the
  Snapshot writer feeds them and stores what they return as rename candidates;
- ``merge_objects`` folds an added object into a removed one, so the removed object keeps
  its identity, descriptions and mappings under the new name. It is what confirming a
  candidate and a manual "merge removed X into added Y" both run.

A merge points the added object's Snapshot rows at the surviving identity, so every
Snapshot that had either object now shows one object that changed its name. The
children of a table or Database Schema are merged by name where both sides have one
and moved over otherwise.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..tables import (
    RenameCandidateRecord,
    SnapshotColumnRecord,
    SnapshotConstraintRecord,
    SnapshotDbSchemaRecord,
    SnapshotIndexRecord,
    SnapshotRoutineRecord,
    SnapshotTableRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcRoutineRecord,
    SrcTableRecord,
)

TABLE_OVERLAP = 0.6
"""The share of column names two tables (or table names two Database Schemas) must have in
common to be proposed as a rename."""


@dataclass(frozen=True)
class ColumnShape:
    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    data_type: str
    ordinal: int


@dataclass(frozen=True)
class TableShape:
    id: uuid.UUID
    schema_id: uuid.UUID
    name: str
    kind: str
    columns: frozenset[str]
    """The column names, in lower case."""


@dataclass(frozen=True)
class SchemaShape:
    id: uuid.UUID
    name: str
    tables: frozenset[str]
    """The table names, in lower case."""


@dataclass(frozen=True)
class Match:
    old_id: uuid.UUID
    new_id: uuid.UUID
    new_name: str
    confidence: float


def _likeness(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _pair[T: (ColumnShape, TableShape, SchemaShape)](
    removed: Iterable[T], added: Iterable[T], score: Callable[[T, T], float | None]
) -> list[Match]:
    """Best score first, each object in at most one pair; ``score`` is ``None`` for a pair
    that cannot be a rename."""
    scored = []
    for old in removed:
        for new in added:
            if (value := score(old, new)) is not None:
                scored.append((value, old, new))
    scored.sort(key=lambda s: (-s[0], s[1].name, s[2].name))
    used_old: set[uuid.UUID] = set()
    used_new: set[uuid.UUID] = set()
    matches = []
    for value, old, new in scored:
        if old.id in used_old or new.id in used_new:
            continue
        used_old.add(old.id)
        used_new.add(new.id)
        matches.append(Match(old.id, new.id, new.name, round(value, 2)))
    return matches


def column_matches(removed: list[ColumnShape], added: list[ColumnShape]) -> list[Match]:
    """Same table, same type and same position; the names' likeness sets the confidence
    between 0.5 and 1."""

    def score(old: ColumnShape, new: ColumnShape) -> float | None:
        if (old.table_id, old.data_type, old.ordinal) != (new.table_id, new.data_type, new.ordinal):
            return None
        return 0.5 + 0.5 * _likeness(old.name, new.name)

    return _pair(removed, added, score)


def table_matches(removed: list[TableShape], added: list[TableShape]) -> list[Match]:
    """Same Database Schema and kind, and at least ``TABLE_OVERLAP`` of the column names
    in common."""

    def score(old: TableShape, new: TableShape) -> float | None:
        if (old.schema_id, old.kind) != (new.schema_id, new.kind):
            return None
        overlap = _overlap(old.columns, new.columns)
        if overlap < TABLE_OVERLAP:
            return None
        return 0.7 * overlap + 0.3 * _likeness(old.name, new.name)

    return _pair(removed, added, score)


def schema_matches(removed: list[SchemaShape], added: list[SchemaShape]) -> list[Match]:
    """At least ``TABLE_OVERLAP`` of the table names in common."""

    def score(old: SchemaShape, new: SchemaShape) -> float | None:
        overlap = _overlap(old.tables, new.tables)
        if overlap < TABLE_OVERLAP:
            return None
        return 0.7 * overlap + 0.3 * _likeness(old.name, new.name)

    return _pair(removed, added, score)


# -- merging ------------------------------------------------------------------------------

_RECORDS: dict[str, type] = {
    "db_schema": SrcDbSchemaRecord,
    "table": SrcTableRecord,
    "column": SrcColumnRecord,
}
_SNAPSHOT_ROWS: dict[str, tuple[type, Any]] = {
    "db_schema": (SnapshotDbSchemaRecord, SnapshotDbSchemaRecord.src_db_schema_id),
    "table": (SnapshotTableRecord, SnapshotTableRecord.src_table_id),
    "column": (SnapshotColumnRecord, SnapshotColumnRecord.src_column_id),
}


class MergeError(Exception):
    """Why two objects cannot be merged; the message is safe to show."""


def _snapshots_with(db: Session, object_type: str, object_id: uuid.UUID) -> set[uuid.UUID]:
    rows, key = _SNAPSHOT_ROWS[object_type]
    return set(db.scalars(sa.select(rows.snapshot_id).where(key == object_id)))  # type: ignore[attr-defined]


def _adopt(old: Any, new: Any, *, with_definition: bool) -> None:
    old.name = new.name
    old.status = "present"
    if with_definition:
        old.current_definition = new.current_definition
        old.version += 1


def _move_snapshot_rows(db: Session, model: type, column: str, old_id: Any, new_id: Any) -> None:
    db.execute(
        sa.update(model).where(getattr(model, column) == new_id).values({column: old_id}),
        execution_options={"synchronize_session": False},
    )


def _merge_column(db: Session, old: SrcColumnRecord, new: SrcColumnRecord, touched: set) -> None:
    _move_snapshot_rows(db, SnapshotColumnRecord, "src_column_id", old.id, new.id)
    touched |= {old.id, new.id}
    db.delete(new)
    db.flush()
    _adopt(old, new, with_definition=True)


def _merge_table(
    db: Session,
    old: SrcTableRecord,
    new: SrcTableRecord,
    schema_id: uuid.UUID,
    touched: set,
) -> None:
    old_columns = {
        c.name: c
        for c in db.scalars(sa.select(SrcColumnRecord).where(SrcColumnRecord.table_id == old.id))
    }
    for column in list(
        db.scalars(sa.select(SrcColumnRecord).where(SrcColumnRecord.table_id == new.id))
    ):
        if (survivor := old_columns.get(column.name)) is not None:
            _merge_column(db, survivor, column, touched)
        else:
            column.table_id = old.id
    db.flush()
    for model in (SnapshotColumnRecord, SnapshotTableRecord, SnapshotIndexRecord):
        _move_snapshot_rows(db, model, "src_table_id", old.id, new.id)
    _move_snapshot_rows(db, SnapshotConstraintRecord, "src_table_id", old.id, new.id)
    _move_snapshot_rows(db, SnapshotConstraintRecord, "ref_table_id", old.id, new.id)
    touched |= {old.id, new.id}
    kind, name, definition = new.kind, new.name, new.current_definition
    db.delete(new)
    db.flush()
    old.kind = kind
    old.db_schema_id = schema_id
    old.name = name
    old.current_definition = definition
    old.status = "present"
    old.version += 1


def _merge_schema(
    db: Session, old: SrcDbSchemaRecord, new: SrcDbSchemaRecord, touched: set
) -> None:
    old_tables = {
        t.name: t
        for t in db.scalars(sa.select(SrcTableRecord).where(SrcTableRecord.db_schema_id == old.id))
    }
    for table in list(
        db.scalars(sa.select(SrcTableRecord).where(SrcTableRecord.db_schema_id == new.id))
    ):
        if (survivor := old_tables.get(table.name)) is not None:
            _merge_table(db, survivor, table, old.id, touched)
        else:
            table.db_schema_id = old.id
    old_routines = {
        (r.name, r.kind, r.signature): r
        for r in db.scalars(
            sa.select(SrcRoutineRecord).where(SrcRoutineRecord.db_schema_id == old.id)
        )
    }
    for routine in list(
        db.scalars(sa.select(SrcRoutineRecord).where(SrcRoutineRecord.db_schema_id == new.id))
    ):
        survivor = old_routines.get((routine.name, routine.kind, routine.signature))
        if survivor is None:
            routine.db_schema_id = old.id
        else:
            _move_snapshot_rows(
                db, SnapshotRoutineRecord, "src_routine_id", survivor.id, routine.id
            )
            db.delete(routine)
    db.flush()
    _move_snapshot_rows(db, SnapshotDbSchemaRecord, "src_db_schema_id", old.id, new.id)
    touched |= {old.id, new.id}
    db.delete(new)
    db.flush()
    _adopt(old, new, with_definition=False)


def merge_objects(
    db: Session, system_id: uuid.UUID, object_type: str, old_id: uuid.UUID, new_id: uuid.UUID
) -> tuple[Any, str]:
    """Fold the added object ``new_id`` into the removed object ``old_id`` of the Source
    System, so the removed one keeps its identity under the added one's name and definition.
    Returns the surviving object and its previous name; raises ``MergeError`` when the two
    are not a removed and an added object of the same kind (and, for a column, table)."""
    record_class = _RECORDS[object_type]
    old = db.get(record_class, old_id, with_for_update=True)
    new = db.get(record_class, new_id, with_for_update=True)
    if old is None or new is None or old_id == new_id:
        raise MergeError("Both objects must exist and be different.")
    if (
        _system_of(db, object_type, old) != system_id
        or _system_of(db, object_type, new) != system_id
    ):
        raise MergeError("Both objects must belong to this Source System.")
    if old.status != "source_removed":
        raise MergeError("The first object must be one that is gone from the source.")
    if new.status != "present":
        raise MergeError("The second object must be one that is present in the source.")
    if _snapshots_with(db, object_type, old_id) & _snapshots_with(db, object_type, new_id):
        raise MergeError("The second object is not new: both were in the same Snapshot.")
    if object_type == "column" and old.table_id != new.table_id:
        raise MergeError("The two columns must belong to the same table.")
    if object_type == "table" and (old.kind != new.kind):
        raise MergeError("A table cannot be merged with a view.")
    previous_name = old.name
    touched: set[uuid.UUID] = set()
    if object_type == "column":
        _merge_column(db, old, new, touched)
    elif object_type == "table":
        _merge_table(db, old, new, new.db_schema_id, touched)
    else:
        _merge_schema(db, old, new, touched)
    # Suggestions about objects that no longer exist, or that just got resolved, are moot.
    db.execute(
        sa.delete(RenameCandidateRecord).where(
            RenameCandidateRecord.status == "suggested",
            sa.or_(
                RenameCandidateRecord.old_object_id.in_(touched),
                RenameCandidateRecord.new_object_id.in_(touched),
            ),
        )
    )
    return old, previous_name


def _system_of(db: Session, object_type: str, record: Any) -> uuid.UUID:
    if object_type == "db_schema":
        return record.source_system_id
    if object_type == "table":
        return db.get(SrcDbSchemaRecord, record.db_schema_id).source_system_id  # type: ignore[union-attr]
    table = db.get(SrcTableRecord, record.table_id)
    return db.get(SrcDbSchemaRecord, table.db_schema_id).source_system_id  # type: ignore[union-attr]
