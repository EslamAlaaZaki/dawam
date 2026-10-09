"""A KPI's formula SQL and links, checked against the DW Schema (spec §6.13).

Formula SQL is one ``SELECT`` query written against the Core and Mart tables of the Data
Warehouse; sqlglot parses it in the target platform's dialect and every table and column it
names must exist there. Before the Data Warehouse is set up there is no schema to check
against, so the text is only parsed. A link points at a Core or Mart column, at the highest
Layer that holds the measure. All functions run in the caller's session.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
import sqlglot
from sqlalchemy.orm import Session
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dawam.modules.warehouse import (
    SQL_DIALECTS,
    DwSchema,
    SchemaColumn,
    SchemaTable,
    clear_kpi_edges,
    describe_columns,
    mart_columns_reading,
    read_schema,
    replace_kpi_edges,
)
from dawam.platform.errors import ApiError

from .tables import MAX_LINKS, KpiLinkRecord

MAX_PROBLEMS = 5
"""How many problems a refusal names."""


def _refused(message: str) -> ApiError:
    return ApiError(422, "invalid_kpi", message, {"field": "formula_sql"})


def _table_of(schema: DwSchema, table: exp.Table) -> SchemaTable | None:
    """The Core or Mart table a reference names; an unqualified name means the highest Layer."""
    name = table.name.lower()
    found = [t for t in schema.tables if t.name.lower() == name]
    if table.db:
        found = [
            t for t in found if schema.layer_schemas.get(t.layer, "").lower() == table.db.lower()
        ]
    return max(found, key=lambda t: t.layer == "mart", default=None)


def validate_formula(schema: DwSchema | None, sql: str) -> None:
    """422 ``invalid_kpi`` (field ``formula_sql``) unless ``sql`` is one SELECT query that
    fits ``schema`` (``None``: only parsed, with the platform unknown)."""
    dialect = SQL_DIALECTS.get(schema.platform) if schema is not None else None
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except SqlglotError as exc:
        raise _refused(f"The formula is not valid SQL: {str(exc).splitlines()[0]}") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise _refused("The formula must be one SELECT query.")
    if schema is None:
        return
    tree = statements[0]
    opaque = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    opaque |= {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    outputs = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    problems: list[str] = []
    sources: dict[str, SchemaTable] = {}
    for reference in tree.find_all(exp.Table):
        if not isinstance(reference.this, exp.Identifier):
            continue  # a table function
        if not reference.db and reference.name.lower() in opaque:
            continue  # a CTE
        table = _table_of(schema, reference)
        if table is None:
            problems.append(f"`{reference.name}` is not a Core or Mart table")
        else:
            sources[(reference.alias or reference.name).lower()] = table
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        name = column.name.lower()
        if column.table:
            qualifier = column.table.lower()
            if qualifier in opaque:
                continue
            table = sources.get(qualifier)
            if table is None:
                problems.append(f"`{column.table}` is not a table or alias in the query")
            elif name not in {c.name.lower() for c in table.columns}:
                problems.append(f"`{table.name}` has no column `{column.name}`")
        elif name not in outputs and not opaque:
            if not any(name in {c.name.lower() for c in t.columns} for t in sources.values()):
                problems.append(f"No table in the query has a column `{column.name}`")
    if problems:
        shown = list(dict.fromkeys(problems))[:MAX_PROBLEMS]
        raise _refused("The formula does not fit the DW Schema: " + "; ".join(shown) + ".")


def check_links(
    db: Session, workspace_id: uuid.UUID, column_ids: Sequence[uuid.UUID]
) -> list[SchemaColumn]:
    """The columns to link, in Layer, table and position order. 404 ``not_set_up`` when
    there is no Data Warehouse; 422 ``invalid_kpi_link`` for more than ``MAX_LINKS``, a column
    that is not a Core or Mart column of the Data Warehouse, or a Core column a Mart column
    is already mapped from (link the Mart column)."""
    ids = list(dict.fromkeys(column_ids))
    if not ids:
        return []
    if len(ids) > MAX_LINKS:
        raise ApiError(422, "invalid_kpi_link", f"A KPI links to at most {MAX_LINKS} columns.")
    found = describe_columns(db, workspace_id, ids)
    if len(found) != len(ids):
        if read_schema(db, workspace_id) is None:
            raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
        raise ApiError(
            422,
            "invalid_kpi_link",
            "A KPI links to Core or Mart columns of the Data Warehouse.",
            {"missing": [str(i) for i in ids if i not in {c.id for c in found}]},
        )
    higher = mart_columns_reading(db, [c.id for c in found if c.layer == "core"])
    if higher:
        named = ", ".join(
            f"{c.table_name}.{c.name} (use {higher[c.id]})" for c in found if c.id in higher
        )
        raise ApiError(
            422,
            "invalid_kpi_link",
            f"Link the highest Layer that holds the measure: {named}.",
        )
    return found


def link_ids(db: Session, kpi_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.scalars(sa.select(KpiLinkRecord.dw_column_id).where(KpiLinkRecord.kpi_id == kpi_id))
    )


def write_links(db: Session, kpi_id: uuid.UUID, column_ids: Sequence[uuid.UUID]) -> None:
    """Make the KPI's links, and the lineage edges they stand for, exactly these columns."""
    db.execute(sa.delete(KpiLinkRecord).where(KpiLinkRecord.kpi_id == kpi_id))
    db.add_all(KpiLinkRecord(kpi_id=kpi_id, dw_column_id=i) for i in column_ids)
    db.flush()
    replace_kpi_edges(db, kpi_id, column_ids)


def drop_links(db: Session, kpi_id: uuid.UUID) -> None:
    clear_kpi_edges(db, kpi_id)


def described(db: Session, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> list[SchemaColumn]:
    return describe_columns(db, workspace_id, link_ids(db, kpi_id))
