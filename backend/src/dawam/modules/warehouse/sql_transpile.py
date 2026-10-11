"""Moving stored SQL text between target platforms' dialects (spec §6.9 "Dialect", story 87).

SQL text (a mapping's expression, a branch's driving input, joins, filters, GROUP BY and HAVING,
a KPI's formula) is stored in the Data Warehouse's target-platform dialect. A platform change
re-expresses it with sqlglot. Text sqlglot cannot translate faithfully (it does not parse, or
the target dialect has no equivalent and sqlglot says so) is never guessed at: the result
carries the reason, and the caller flags the text "manual".

Pure functions, no database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from .lineage_sql import DIALECTS
from .platforms import TargetPlatform

FragmentKind = Literal["expression", "table", "joins", "filter", "group_by", "having", "query"]

_WRAP: dict[FragmentKind, str] = {
    "expression": "SELECT {t}",
    "table": "SELECT 1 FROM {t}",
    "joins": "SELECT 1 FROM __t {t}",
    "filter": "SELECT 1 FROM __t WHERE {t}",
    "group_by": "SELECT 1 FROM __t GROUP BY {t}",
    "having": "SELECT 1 FROM __t GROUP BY 1 HAVING {t}",
    "query": "{t}",
}


@dataclass(frozen=True)
class Transpiled:
    sql: str | None
    """The text in the target dialect; ``None`` when it needs a human (see ``manual``)."""
    manual: str | None = None
    """Why sqlglot could not translate it."""
    changed: bool = False
    """The target dialect spells it differently (a text both dialects spell the same needs
    no change)."""


def _pieces(kind: FragmentKind, tree: exp.Expression) -> list[exp.Expression]:
    if kind == "expression":
        found = tree.expressions if isinstance(tree, exp.Select) else []
        if len(found) != 1:
            raise ValueError("Expected one SQL expression.")
        return list(found)
    if kind == "query":
        return [tree]
    if not isinstance(tree, exp.Select):
        raise ValueError("Expected a fragment of a query.")
    if kind == "table":
        source = tree.args.get("from_") or tree.args.get("from")
        if source is None or not isinstance(source.this, exp.Expression):
            raise ValueError("Expected a table.")
        return [source.this]
    if kind == "joins":
        joins = tree.args.get("joins") or []
        if not joins:
            raise ValueError("Expected JOIN clauses.")
        return list(joins)
    if kind == "filter":
        where = tree.args.get("where")
    elif kind == "having":
        where = tree.args.get("having")
    else:
        group = tree.args.get("group")
        if group is None or not group.expressions:
            raise ValueError("Expected GROUP BY expressions.")
        return list(group.expressions)
    if where is None or not isinstance(where.this, exp.Expression):
        raise ValueError("Expected a condition.")
    return [where.this]


def _render(kind: FragmentKind, tree: exp.Expression, dialect: str | None, *, strict: bool) -> str:
    separator = " " if kind == "joins" else ", "
    level = ErrorLevel.RAISE if strict else ErrorLevel.IGNORE
    return separator.join(
        piece.sql(dialect=dialect, unsupported_level=level) for piece in _pieces(kind, tree)
    )


def transpile(
    kind: FragmentKind, text: str, source: TargetPlatform, target: TargetPlatform
) -> Transpiled:
    """``text`` (a ``kind`` fragment written for ``source``) in ``target``'s dialect."""
    read, write = DIALECTS.get(source), DIALECTS.get(target)
    try:
        tree = sqlglot.parse_one(_WRAP[kind].format(t=text.strip().rstrip(";")), read=read)
        spelled = _render(kind, tree, read, strict=False)
        translated = _render(kind, tree, write, strict=True)
    except (SqlglotError, ValueError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else "The text could not be parsed."
        return Transpiled(None, f"{source} to {target}: {reason}")
    return Transpiled(translated, changed=translated != spelled)
