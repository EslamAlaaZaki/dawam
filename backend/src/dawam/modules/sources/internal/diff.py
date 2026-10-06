"""Comparing two Snapshots (spec §6.3, §7 "Source identity vs Snapshots").

Both Snapshots point at the same stable Source Objects, so objects are paired by identity
id: an id only in the newer Snapshot is ``added``, only in the older ``removed``, and in
both with a different definition ``changed`` (a rename or a type change shows as a change,
not as a remove plus an add). The Snapshots are read as they were stored: this never looks
at a Source Object's state now.

The functions take the service's Snapshot content by duck typing, so the module needs
nothing from the service that calls it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class ColumnChange:
    id: uuid.UUID
    name: str
    """The newer name, or the older one for a removed column."""
    change: str
    """``added``, ``removed`` or ``changed``."""
    fields: list[FieldChange] = field(default_factory=list)


@dataclass(frozen=True)
class TableChange:
    id: uuid.UUID
    db_schema: str
    name: str
    kind: str
    change: str
    fields: list[FieldChange] = field(default_factory=list)
    columns: list[ColumnChange] = field(default_factory=list)
    """Added, removed and changed columns; empty for an added or removed table."""


@dataclass(frozen=True)
class RoutineChange:
    id: uuid.UUID
    db_schema: str
    name: str
    kind: str
    signature: str
    change: str
    fields: list[FieldChange] = field(default_factory=list)


@dataclass(frozen=True)
class DbSchemaChange:
    id: uuid.UUID
    name: str
    change: str
    fields: list[FieldChange] = field(default_factory=list)


@dataclass(frozen=True)
class SuspectedPiiColumn:
    """A column new in the newer Snapshot that has a ``suggested`` or ``confirmed`` PII
    finding (spec story 139). Never holds a value."""

    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    category: str
    confidence: float
    status: str


@dataclass(frozen=True)
class SnapshotDiff:
    from_snapshot_id: uuid.UUID
    to_snapshot_id: uuid.UUID
    db_schemas: list[DbSchemaChange]
    tables: list[TableChange]
    routines: list[RoutineChange]
    suspected_pii: list[SuspectedPiiColumn] = field(default_factory=list)
    """New suspected PII columns; filled in by the service, which can read the findings."""


_COLUMN_FIELDS = ("name", "ordinal", "data_type", "is_nullable", "is_pk", "default", "comment")
_TABLE_FIELDS = ("db_schema", "name", "kind", "view_definition", "comment")
_ROUTINE_FIELDS = ("db_schema", "name", "definition")


def _changed(old: Any, new: Any, names: Iterable[str]) -> list[FieldChange]:
    return [
        FieldChange(
            name,
            None if getattr(old, name) is None else str(getattr(old, name)),
            None if getattr(new, name) is None else str(getattr(new, name)),
        )
        for name in names
        if getattr(old, name) != getattr(new, name)
    ]


def _pair[T](
    old: Iterable[T],
    new: Iterable[T],
    names: Iterable[str],
    build: Callable[[T, str, list[FieldChange]], Any],
) -> list[Any]:
    """``build(object, change, fields)`` for every identity that differs between ``old`` and
    ``new``; the caller sorts them."""
    before: Mapping[uuid.UUID, T] = {o.id: o for o in old}  # type: ignore[attr-defined]
    after: Mapping[uuid.UUID, T] = {n.id: n for n in new}  # type: ignore[attr-defined]
    names = tuple(names)
    changes = [build(n, "added", []) for key, n in after.items() if key not in before]
    changes += [build(o, "removed", []) for key, o in before.items() if key not in after]
    for key, n in after.items():
        if key in before and (fields := _changed(before[key], n, names)):
            changes.append(build(n, "changed", fields))
    return changes


def diff_snapshots(old: Any, new: Any) -> SnapshotDiff:
    """What changed from the Snapshot content ``old`` to ``new``."""
    schemas = _pair(
        old.db_schemas,
        new.db_schemas,
        ("name",),
        lambda s, change, fields: DbSchemaChange(s.id, s.name, change, fields),
    )
    old_tables = {t.id: t for t in old.tables}
    new_tables = {t.id: t for t in new.tables}
    tables = _pair(
        old.tables,
        new.tables,
        _TABLE_FIELDS,
        lambda t, change, fields: TableChange(
            t.id, t.db_schema, t.name, t.kind, change, fields, []
        ),
    )
    # Column changes can exist on a table whose own fields did not change.
    by_id = {t.id: t for t in tables}
    for table_id, table in new_tables.items():
        previous = old_tables.get(table_id)
        if previous is None:
            continue
        columns = _pair(
            previous.columns,
            table.columns,
            _COLUMN_FIELDS,
            lambda c, change, fields: ColumnChange(c.id, c.name, change, fields),
        )
        if not columns:
            continue
        columns.sort(key=lambda c: (c.name, c.change))
        entry = by_id.get(table_id)
        if entry is None:
            entry = TableChange(
                table.id, table.db_schema, table.name, table.kind, "changed", [], []
            )
            tables.append(entry)
            by_id[table_id] = entry
        entry.columns.extend(columns)
    routines = _pair(
        old.routines,
        new.routines,
        _ROUTINE_FIELDS,
        lambda r, change, fields: RoutineChange(
            r.id, r.db_schema, r.name, r.kind, r.signature, change, fields
        ),
    )
    schemas.sort(key=lambda s: (s.name, s.change))
    tables.sort(key=lambda t: (t.db_schema, t.name, t.change))
    routines.sort(key=lambda r: (r.db_schema, r.name, r.signature, r.change))
    return SnapshotDiff(
        from_snapshot_id=old.snapshot.id,
        to_snapshot_id=new.snapshot.id,
        db_schemas=schemas,
        tables=tables,
        routines=routines,
    )
