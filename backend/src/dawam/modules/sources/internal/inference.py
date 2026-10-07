"""The rules of relationship inference (spec §6.6, stories 58, 59). Pure: no database, no
source access, so every rule is checked on its own.

A candidate pair is a ``from`` column that may reference a ``to`` column, scored from
five signals, each of which contributes to a confidence between 0 and 1:

- name similarity (``customer_id`` ~ ``customers.id``, ``cust_id``, equal key names);
- type compatibility;
- the target being unique (a PK or unique index, or profiled distinct = rows);
- the value overlap of a sample (only with a live Connection and profiling);
- a JOIN condition between the two columns in a view or routine definition, parsed here
  with sqlglot.

``find_candidates`` only proposes pairs with a name or a JOIN signal: type and
uniqueness alone would pair every integer column with every key. Overlap then refines
the score; it never creates a pair.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

DEFAULT_THRESHOLD = 0.6

# Without a measured overlap the three static signals weigh 0.40 / 0.15 / 0.20 (at most
# 0.75); once overlap is measured it takes the largest share, so a pair whose values do
# not overlap falls below the default threshold however well the names match.
NAME_WEIGHT = 0.40
TYPE_WEIGHT = 0.15
UNIQUE_WEIGHT = 0.20
MEASURED_NAME_WEIGHT = 0.30
MEASURED_TYPE_WEIGHT = 0.10
MEASURED_UNIQUE_WEIGHT = 0.15
OVERLAP_WEIGHT = 0.45
JOIN_WEIGHT = 0.45

_EPSILON = 1e-9

# -- names ----------------------------------------------------------------------------

_KEY_SUFFIXES = ("id", "key")
_GENERIC_KEYS = ("id", "key")


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[^\w]+|_", spaced.lower()) if t]


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def name_similarity(from_column: str, to_table: str, to_column: str) -> float:
    """0 to 1: how much the names suggest ``from_column`` references ``to_table.to_column``.

    Against a generic key (``id``) the column must be ``<table>_id`` (1.0) or an
    abbreviation of the table (``cust_id``: 0.8); against a named key the names must be
    equal (0.9). A bare ``id`` says nothing."""
    from_tokens = _tokens(from_column)
    to_tokens = _tokens(to_column)
    if not from_tokens or from_tokens == ["id"]:
        return 0.0
    if to_tokens and to_tokens[-1] in _GENERIC_KEYS and len(to_tokens) == 1:
        if len(from_tokens) < 2 or from_tokens[-1] not in _KEY_SUFFIXES:
            return 0.0
        stem = _singular("".join(from_tokens[:-1]))
        table = _singular("".join(_tokens(to_table)))
        if stem == table:
            return 1.0
        if len(stem) >= 3 and table.startswith(stem):
            return 0.8
        return 0.0
    return 0.9 if from_tokens == to_tokens else 0.0


# -- types ----------------------------------------------------------------------------

_FAMILIES: Mapping[str, str] = {
    **dict.fromkeys(
        (
            "int",
            "integer",
            "bigint",
            "smallint",
            "tinyint",
            "mediumint",
            "int2",
            "int4",
            "int8",
            "serial",
            "bigserial",
            "smallserial",
        ),
        "integer",
    ),
    **dict.fromkeys(
        (
            "numeric",
            "decimal",
            "number",
            "money",
            "float",
            "real",
            "double",
            "double precision",
            "float4",
            "float8",
        ),
        "numeric",
    ),
    **dict.fromkeys(
        (
            "text",
            "varchar",
            "character varying",
            "char",
            "character",
            "bpchar",
            "nvarchar",
            "nchar",
            "varchar2",
            "nvarchar2",
            "ntext",
            "citext",
            "string",
            "tinytext",
            "mediumtext",
            "longtext",
        ),
        "string",
    ),
    **dict.fromkeys(("uuid", "uniqueidentifier"), "uuid"),
    "date": "date",
    **dict.fromkeys(
        (
            "timestamp",
            "timestamptz",
            "timestamp with time zone",
            "timestamp without time zone",
            "datetime",
            "datetime2",
            "smalldatetime",
            "datetimeoffset",
        ),
        "timestamp",
    ),
}


def _base_type(data_type: str) -> str:
    return " ".join(re.sub(r"\(.*?\)", "", data_type).lower().split())


def type_match(a: str, b: str) -> float:
    """1.0 for the same base type (width and precision ignored), 0.8 for the same family
    (``integer`` and ``bigint``), 0 otherwise."""
    base_a, base_b = _base_type(a), _base_type(b)
    if base_a == base_b:
        return 1.0
    family_a, family_b = _FAMILIES.get(base_a), _FAMILIES.get(base_b)
    return 0.8 if family_a is not None and family_a == family_b else 0.0


# -- score ----------------------------------------------------------------------------


def score(
    *, name: float, type_: float, unique: float, overlap: float | None, joined: bool
) -> float:
    """The confidence: each signal's weight times its strength, at most 1. ``overlap`` is
    ``None`` when it was not measured (no live Connection, or an unprofiled column)."""
    if overlap is None:
        total = NAME_WEIGHT * name + TYPE_WEIGHT * type_ + UNIQUE_WEIGHT * unique
    else:
        total = (
            MEASURED_NAME_WEIGHT * name
            + MEASURED_TYPE_WEIGHT * type_
            + MEASURED_UNIQUE_WEIGHT * unique
            + OVERLAP_WEIGHT * overlap
        )
    return round(min(1.0, total + (JOIN_WEIGHT if joined else 0.0)), 4)


# -- JOIN conditions ------------------------------------------------------------------


@dataclass(frozen=True)
class JoinSide:
    """One side of a JOIN condition, as written in the text (names not yet resolved)."""

    schema: str | None
    table: str
    column: str


_STARTS = re.compile(r"\b(SELECT|UPDATE|DELETE|INSERT|MERGE)\b", re.IGNORECASE)
_TOKEN = re.compile(
    r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/|\$\w*\$|;|\(|\)|\bCASE\b|\bEND\b", re.IGNORECASE | re.DOTALL
)
_SCOPES = (exp.Select, exp.Update, exp.Delete)


def _statements(text: str) -> Iterable[str]:
    """Candidate statements inside a view or routine definition: each starts at a DML
    keyword and ends at the first ``;``, dollar quote, unmatched ``)`` or block ``END``."""
    for start in _STARTS.finditer(text):
        depth = cases = 0
        end = len(text)
        for token in _TOKEN.finditer(text, start.start()):
            word = token.group().upper()
            if word == "(":
                depth += 1
            elif word == ")":
                depth -= 1
                if depth < 0:
                    end = token.start()
                    break
            elif word == "CASE":
                cases += 1
            elif word == "END":
                if cases:
                    cases -= 1
                else:
                    end = token.start()
                    break
            elif word == ";" or word.startswith("$"):
                end = token.start()
                break
        yield text[start.start() : end]


def _own_scope(node: exp.Expression) -> exp.Expression | None:
    return node.find_ancestor(*_SCOPES)


def _in_condition(node: exp.Expression, scope: exp.Expression) -> bool:
    """Whether ``node`` sits in a ``JOIN ... ON`` or ``WHERE`` of ``scope`` (an equality
    in an UPDATE's ``SET`` is an assignment, not a join)."""
    parent = node.parent
    while parent is not None and parent is not scope:
        if isinstance(parent, (exp.Join, exp.Where)):
            return True
        parent = parent.parent
    return False


def routine_joins(text: str, dialect: str | None) -> list[tuple[JoinSide, JoinSide]]:
    """The equality JOIN conditions between columns of two different tables in the
    statements of a view or routine definition, with aliases resolved. Text that does not
    parse is skipped, never an error."""
    found: dict[frozenset[JoinSide], tuple[JoinSide, JoinSide]] = {}
    for statement in _statements(text or ""):
        try:
            tree = parse_one(statement, read=dialect)
        except (SqlglotError, RecursionError):
            continue
        for scope in tree.find_all(*_SCOPES):
            aliases: dict[str, tuple[str | None, str]] = {}
            for table in scope.find_all(exp.Table):
                if _own_scope(table) is scope and table.name:
                    aliases[table.alias_or_name.lower()] = (table.db or None, table.name)
            for eq in scope.find_all(exp.EQ):
                if _own_scope(eq) is not scope or not _in_condition(eq, scope):
                    continue
                left, right = eq.left, eq.right
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                a = _side(left, aliases)
                b = _side(right, aliases)
                if a is None or b is None or (a.schema, a.table) == (b.schema, b.table):
                    continue
                found.setdefault(frozenset((a, b)), (a, b))
    return list(found.values())


def _side(column: exp.Column, aliases: Mapping[str, tuple[str | None, str]]) -> JoinSide | None:
    if not column.table or not column.name:
        return None
    resolved = aliases.get(column.table.lower())
    if resolved is None:
        return None
    return JoinSide(resolved[0], resolved[1], column.name)


# -- candidates -----------------------------------------------------------------------


@dataclass(frozen=True)
class Col:
    """A present column of a base table, as the latest Snapshot has it."""

    id: uuid.UUID
    table_id: uuid.UUID
    schema: str
    table: str
    name: str
    data_type: str
    unique_via: str | None
    """``primary key``, ``unique index``, ``profiled: distinct = rows`` or ``None``."""

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}.{self.name}"


@dataclass
class Candidate:
    from_col: Col
    to_col: Col
    name: float
    type_: float
    unique: float
    joins: list[str] = field(default_factory=list)
    """The views and routines (``schema.name``) whose text joins the two columns."""
    overlap: float | None = None
    overlap_sample: tuple[int, int] | None = None
    """Distinct source values checked, and rows sampled from the target."""

    @property
    def confidence(self) -> float:
        return score(
            name=self.name,
            type_=self.type_,
            unique=self.unique,
            overlap=self.overlap,
            joined=bool(self.joins),
        )

    def best_case(self) -> float:
        """The confidence if the measured overlap turned out to be complete."""
        return score(
            name=self.name,
            type_=self.type_,
            unique=self.unique,
            overlap=1.0,
            joined=bool(self.joins),
        )

    @property
    def origin(self) -> str:
        return "routine" if self.joins else "inferred"

    def evidence(self, threshold: float) -> dict[str, Any]:
        """Why the pair was proposed. Names and ratios only, never a data value."""
        signals: dict[str, Any] = {
            "name": {
                "score": self.name,
                "detail": f"{self.from_col.name} ~ {self.to_col.table}.{self.to_col.name}",
            },
            "type": {
                "score": self.type_,
                "detail": f"{self.from_col.data_type} / {self.to_col.data_type}",
            },
            "unique": {
                "score": self.unique,
                "detail": self.to_col.unique_via or "the target is not known to be unique",
            },
        }
        if self.joins:
            signals["join"] = {"score": 1.0, "objects": sorted(self.joins)}
        if self.overlap is not None and self.overlap_sample is not None:
            signals["overlap"] = {
                "ratio": round(self.overlap, 4),
                "values_checked": self.overlap_sample[0],
                "target_rows_sampled": self.overlap_sample[1],
            }
        return {"signals": signals, "threshold": threshold}


def find_candidates(
    columns: Iterable[Col],
    joins: Mapping[tuple[uuid.UUID, uuid.UUID], list[str]],
    declared: set[frozenset[uuid.UUID]],
    *,
    threshold: float,
) -> list[Candidate]:
    """Pairs with a name or JOIN signal that could reach ``threshold``: overlap is measured
    later, so a pair qualifies if it would with complete overlap.

    ``joins`` maps a column-id pair (either order) to the objects that join them;
    ``declared`` holds the column pairs of declared foreign keys, which are never
    proposed. A joined pair is oriented to its unique side, so ``from`` references ``to``."""
    cols = list(columns)
    targets = [c for c in cols if c.unique_via is not None]
    found: dict[tuple[uuid.UUID, uuid.UUID], Candidate] = {}

    def propose(source: Col, target: Col, joined: list[str]) -> None:
        if frozenset((source.id, target.id)) in declared:
            return
        candidate = Candidate(
            from_col=source,
            to_col=target,
            name=name_similarity(source.name, target.table, target.name),
            type_=type_match(source.data_type, target.data_type),
            unique=_unique_strength(target),
            joins=joined,
        )
        if candidate.type_ == 0.0 or (candidate.name == 0.0 and not joined):
            return
        if candidate.best_case() + _EPSILON >= threshold:
            found[source.id, target.id] = candidate

    for source in cols:
        for target in targets:
            if source.table_id != target.table_id:
                propose(source, target, [])

    by_id = {c.id: c for c in cols}
    for (a_id, b_id), objects in joins.items():
        a, b = by_id.get(a_id), by_id.get(b_id)
        if a is None or b is None or a.table_id == b.table_id:
            continue
        # Orient to the unique side; when both or neither are unique, to the better name.
        a_to_b = name_similarity(a.name, b.table, b.name)
        b_to_a = name_similarity(b.name, a.table, a.name)
        if (a.unique_via is None) == (b.unique_via is None):
            source, target = (a, b) if a_to_b >= b_to_a else (b, a)
        else:
            source, target = (a, b) if b.unique_via is not None else (b, a)
        joined = sorted(set(objects))
        existing = found.get((source.id, target.id))
        if existing is not None:
            existing.joins = sorted(set(existing.joins) | set(joined))
        else:
            propose(source, target, joined)

    # x -> y and y -> x are one relationship: keep the stronger direction.
    for (from_id, to_id), candidate in list(found.items()):
        reverse = found.get((to_id, from_id))
        if reverse is None or (from_id, to_id) not in found:
            continue
        loser = (to_id, from_id) if _rank(candidate) >= _rank(reverse) else (from_id, to_id)
        del found[loser]
    return sorted(
        found.values(), key=lambda c: (-c.confidence, c.from_col.qualified, c.to_col.qualified)
    )


def _unique_strength(target: Col) -> float:
    if target.unique_via is None:
        return 0.0
    return 0.8 if target.unique_via.startswith("profiled") else 1.0


def _rank(candidate: Candidate) -> tuple[float, bool, str]:
    return (candidate.confidence, bool(candidate.joins), candidate.from_col.qualified)
