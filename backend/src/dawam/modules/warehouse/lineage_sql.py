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
        ref = ColumnRef(column.table or None, column.name)
        target = uses if _steering(column) else value
        if ref not in target:
            target.append(ref)
    return ParsedExpression(tuple(value), tuple(uses), isinstance(root, exp.Column))
