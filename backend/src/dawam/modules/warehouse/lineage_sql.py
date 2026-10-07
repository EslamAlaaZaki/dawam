"""Reading a mapping's SQL text for the columns it reads (spec §6.14).

The SQL text is the master: a mapping's inputs are whatever columns the expression
names, written ``table.column`` (the table is a DW Schema table of the Layer below).
sqlglot parses the text in the target platform's dialect; a column is a ``value``
input when it feeds the result and a ``uses`` input when it only steers it (a
``CASE``/``IF`` condition, a filter, a window's partition or order), and a column can
be both.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECTS = {
    "postgresql": "postgres",
    "sqlserver": "tsql",
    "oracle": "oracle",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
}
"""Target platform to sqlglot dialect."""

_NOT_ONE = "Write one SQL expression, not a query."


class Unparsable(Exception):
    """The text is not a single SQL expression."""


@dataclass(frozen=True)
class ColumnRef:
    table: str | None
    column: str


@dataclass(frozen=True)
class ParsedExpression:
    value: tuple[ColumnRef, ...]
    uses: tuple[ColumnRef, ...]
    is_column: bool
    """The whole expression is one bare column (a ``direct`` mapping)."""


def _steering(column: exp.Column) -> bool:
    """Whether the column sits in a condition or a window's partition or order."""
    node: exp.Expression = column
    while node.parent is not None:
        parent, key = node.parent, node.arg_key
        if isinstance(parent, (exp.If, exp.Case)) and key == "this":
            return True
        if isinstance(parent, (exp.Where, exp.Filter)):
            return True
        if isinstance(parent, exp.Window) and key in ("partition_by", "order"):
            return True
        node = parent
    return False


def parse_expression(sql: str, platform: str) -> ParsedExpression:
    """The columns ``sql`` reads. Raises ``Unparsable`` when it is not one expression."""
    try:
        trees = sqlglot.parse(f"SELECT {sql}", read=DIALECTS.get(platform))
    except SqlglotError as exc:
        raise Unparsable(str(exc).splitlines()[0]) from None
    tree = trees[0] if len(trees) == 1 else None
    if not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
        raise Unparsable(_NOT_ONE)
    if any(v for k, v in tree.args.items() if k != "expressions"):
        raise Unparsable(_NOT_ONE)
    root = tree.expressions[0]
    if isinstance(root, (exp.Alias, exp.Subquery, exp.Select)):
        raise Unparsable(_NOT_ONE)
    value: list[ColumnRef] = []
    uses: list[ColumnRef] = []
    for column in root.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            raise Unparsable("A column name is expected, not *.")
        # Unquoted names are case-insensitive: T.a and t.a are one column.
        ref = ColumnRef((column.table or "").lower() or None, column.name.lower())
        target = uses if _steering(column) else value
        if ref not in target:
            target.append(ref)
    return ParsedExpression(tuple(value), tuple(uses), isinstance(root, exp.Column))


@dataclass(frozen=True)
class BranchParts:
    tables: tuple[str, ...]
    """Every table the branch's FROM, joins and conditions name (lower case)."""
    group_by: tuple[str, ...]
    """The ``GROUP BY`` expressions, normalized."""
    group_columns: frozenset[ColumnRef]
    """Every column the ``GROUP BY`` expressions read."""
    refs: tuple[ColumnRef, ...]
    """Every column the joins, filters, GROUP BY and HAVING read (table aliases resolved)."""


def _normal(node: exp.Expression, platform: str) -> str:
    return node.sql(dialect=DIALECTS.get(platform)).lower()


def parse_branch(
    driving_input: str,
    joins: str,
    filters: str,
    group_by: str | None,
    having: str | None,
    platform: str,
) -> BranchParts:
    """Read a branch's SQL parts as the query they make. Raises ``Unparsable``."""
    parts = [f"SELECT 1 FROM {driving_input}", joins]
    if filters:
        parts.append(f"WHERE {filters}")
    if group_by:
        parts.append(f"GROUP BY {group_by}")
    if having:
        parts.append(f"HAVING {having}")
    try:
        trees = sqlglot.parse(" ".join(p for p in parts if p), read=DIALECTS.get(platform))
    except SqlglotError as exc:
        raise Unparsable(str(exc).splitlines()[0]) from None
    tree = trees[0] if len(trees) == 1 else None
    if not isinstance(tree, exp.Select):
        raise Unparsable("The branch must be one query: a driving input, joins and conditions.")
    group = tree.args.get("group")
    group_nodes = list(group.expressions) if group else []
    columns = frozenset(
        ColumnRef((c.table or "").lower() or None, c.name.lower())
        for g in group_nodes
        for c in ([g] if isinstance(g, exp.Column) else [])
    )
    aliases = {t.alias.lower(): t.name.lower() for t in tree.find_all(exp.Table) if t.alias}
    refs: list[ColumnRef] = []
    for c in tree.find_all(exp.Column):
        table = (c.table or "").lower() or None
        ref = ColumnRef(aliases.get(table, table) if table else None, c.name.lower())
        if ref not in refs:
            refs.append(ref)
    return BranchParts(
        tables=tuple(dict.fromkeys(t.name.lower() for t in tree.find_all(exp.Table))),
        group_by=tuple(_normal(g, platform) for g in group_nodes),
        group_columns=columns,
        refs=tuple(refs),
    )


def cast_null(data_type: dict[str, object], platform: str) -> str:
    """``CAST(NULL AS <type>)`` for a neutral column type, in the target dialect."""
    kind = {
        "smallint": exp.DataType.Type.SMALLINT,
        "integer": exp.DataType.Type.INT,
        "bigint": exp.DataType.Type.BIGINT,
        "decimal": exp.DataType.Type.DECIMAL,
        "float": exp.DataType.Type.FLOAT,
        "double": exp.DataType.Type.DOUBLE,
        "boolean": exp.DataType.Type.BOOLEAN,
        "char": exp.DataType.Type.CHAR,
        "string": exp.DataType.Type.VARCHAR,
        "text": exp.DataType.Type.TEXT,
        "binary": exp.DataType.Type.VARBINARY,
        "date": exp.DataType.Type.DATE,
        "time": exp.DataType.Type.TIME,
        "timestamp": exp.DataType.Type.TIMESTAMP,
        "timestamptz": exp.DataType.Type.TIMESTAMPTZ,
        "uuid": exp.DataType.Type.UUID,
        "json": exp.DataType.Type.JSON,
    }.get(str(data_type.get("type")), exp.DataType.Type.TEXT)
    params = []
    if kind == exp.DataType.Type.DECIMAL and data_type.get("precision"):
        params = [data_type["precision"], data_type.get("scale") or 0]
    elif kind in (exp.DataType.Type.CHAR, exp.DataType.Type.VARCHAR, exp.DataType.Type.VARBINARY):
        params = [data_type["length"]] if data_type.get("length") else []
    node = exp.DataType(
        this=kind,
        expressions=[exp.DataTypeParam(this=exp.Literal.number(p)) for p in params],
    )
    return exp.Cast(this=exp.Null(), to=node).sql(dialect=DIALECTS.get(platform))


def is_group_safe(sql: str, parts: BranchParts, platform: str) -> bool:
    """Whether an output expression is valid beside the branch's ``GROUP BY``: every
    column it reads is inside an aggregate or window, inside a grouped expression, or a
    grouped column."""
    try:
        trees = sqlglot.parse(f"SELECT {sql}", read=DIALECTS.get(platform))
    except SqlglotError:
        return True  # unparsed text is reported on its own
    tree = trees[0] if len(trees) == 1 else None
    if not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
        return True
    root = tree.expressions[0]
    return all(_column_safe(c, root, parts, platform) for c in root.find_all(exp.Column))


def _column_safe(
    column: exp.Column, root: exp.Expression, parts: BranchParts, platform: str
) -> bool:
    if ColumnRef((column.table or "").lower() or None, column.name.lower()) in parts.group_columns:
        return True
    node: exp.Expression | None = column
    while node is not None:
        if isinstance(node, (exp.AggFunc, exp.Window)) or _normal(node, platform) in parts.group_by:
            return True
        if node is root:
            break
        node = node.parent
    return False
