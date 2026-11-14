"""The rules of relationship inference (spec §6.6, stories 58, 59, #608). Pure: no
database, no source access, so every rule is checked on its own.

A pair is a ``from`` (child) column that may reference a ``to`` (parent) column. Two
guessers propose pairs side by side:

- the **rules** (``find_candidates``): name similarity (``customer_id`` ~ ``customers.id``,
  ``cust_id``, equal key names) and JOIN conditions in view and routine text (parsed here
  with sqlglot), scored with type compatibility and the target being known unique;
- the **AI** (``AiGuess``, asked by the service): its own confidence and a short reason.

``union`` merges them on the same two columns in either direction, recording what found
each pair (``rules``, ``ai`` or ``both``). With a live Connection, each pair is then
counted on the source (``count_sql``: counts only) and the counts set its confidence
(``counted_confidence``); a count that times out lowers the guess
(``timed_out_confidence``); without counts the confidence is the guess (``guess_score``).
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

# The rule score: name, type and uniqueness weigh 0.40 / 0.15 / 0.20 (at most 0.75), and a
# JOIN in a view or routine adds 0.45.
NAME_WEIGHT = 0.40
TYPE_WEIGHT = 0.15
UNIQUE_WEIGHT = 0.20
JOIN_WEIGHT = 0.45

# The confidence (spec §6.6): the guess is the better of the rule score and the AI's own
# confidence, plus a bonus when both found the pair; an uncounted AI guess is capped so it
# never outranks a counted pair. Counts then weigh 0.7 against the guess's 0.3.
BOTH_BONUS = 0.05
UNCOUNTED_AI_CAP = 0.7
COUNT_WEIGHT = 0.7
NOT_UNIQUE_CONFIDENCE = 0.3
FEW_DISTINCT = 5
"""Fewer distinct child values than this may match any small key by chance."""
FEW_DISTINCT_FACTOR = 0.5
TIMEOUT_PENALTY = 0.15
TIMEOUT_FLOOR = 0.6

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


def score(*, name: float, type_: float, unique: float, joined: bool) -> float:
    """The rule score: each signal's weight times its strength, at most 1."""
    total = NAME_WEIGHT * name + TYPE_WEIGHT * type_ + UNIQUE_WEIGHT * unique
    return round(min(1.0, total + (JOIN_WEIGHT if joined else 0.0)), 4)


def guess_score(rule: float | None, ai: float | None, *, counted: bool) -> float:
    """The better of the rule score and the AI's confidence (``None``: that guesser did not
    find the pair), plus ``BOTH_BONUS`` when both did, at most 1. Without counts the AI's
    confidence is capped at ``UNCOUNTED_AI_CAP``."""
    if ai is not None and not counted:
        ai = min(ai, UNCOUNTED_AI_CAP)
    best = max(rule or 0.0, ai or 0.0)
    if rule is not None and ai is not None:
        best += BOTH_BONUS
    return round(min(1.0, best), 4)


@dataclass(frozen=True)
class Counts:
    """What a count query returned for one direction: numbers only, never a value."""

    target_rows: int
    target_values: int
    """``COUNT(parent)``: the target's non-null values."""
    target_distinct: int
    child_values: int
    """``COUNT(child)``: the child's non-null values."""
    child_distinct: int
    matched_rows: int
    """Child rows whose value is found in the target."""

    @property
    def target_unique(self) -> bool:
        return self.target_values > 0 and self.target_values == self.target_distinct

    @property
    def match_rate(self) -> float:
        if self.child_values <= 0:
            return 0.0
        return min(1.0, self.matched_rows / self.child_values)

    @property
    def orphan_rows(self) -> int:
        """Child rows with a value the target does not have."""
        return max(0, self.child_values - self.matched_rows)


def counted_confidence(guess: float, counts: Counts) -> float:
    """A counted pair's confidence: ``NOT_UNIQUE_CONFIDENCE`` when the target is not unique;
    else 0.7 x the match rate (halved under ``FEW_DISTINCT`` distinct child values) + 0.3 x
    the guess."""
    if not counts.target_unique:
        return NOT_UNIQUE_CONFIDENCE
    base = counts.match_rate
    if counts.child_distinct < FEW_DISTINCT:
        base *= FEW_DISTINCT_FACTOR
    return round(COUNT_WEIGHT * base + (1 - COUNT_WEIGHT) * guess, 4)


def timed_out_confidence(guess: float) -> float:
    """A pair whose count timed out: the guess lowered by ``TIMEOUT_PENALTY``, never below
    ``TIMEOUT_FLOOR`` (so it stays visible, and below what a full count would give)."""
    return round(max(TIMEOUT_FLOOR, guess - TIMEOUT_PENALTY), 4)


def reverse_wins(forward: Counts, reverse: Counts) -> bool:
    """Whether a pair counted both ways reads better the other way round: the direction
    whose target is unique, else the higher match rate."""
    if forward.target_unique != reverse.target_unique:
        return reverse.target_unique
    return reverse.match_rate > forward.match_rate


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
    """The equality JOIN conditions between two columns in the statements of a view or
    routine definition, with aliases resolved: of two tables, or of one table joined to
    itself (a self-reference). Text that does not parse is skipped, never an error."""
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
                if a is None or b is None or a == b:
                    continue
                # One table on both sides is a self-reference only through two aliases;
                # ``a.x = a.y`` compares two columns of one row.
                if (a.schema, a.table) == (b.schema, b.table) and (
                    left.table.lower() == right.table.lower()
                ):
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


@dataclass(frozen=True)
class AiGuess:
    """A pair the AI proposed from metadata: its own confidence (0 to 1) and reason."""

    from_col: Col
    to_col: Col
    confidence: float
    reason: str


COUNT_STATUSES = ("counted", "timed_out", "failed", "no_connection")
_COUNT_NOTES = {
    "timed_out": "count timed out",
    "no_connection": "no live Connection: not counted",
}


@dataclass
class Candidate:
    from_col: Col
    to_col: Col
    name: float
    type_: float
    unique: float
    joins: list[str] = field(default_factory=list)
    """The views and routines (``schema.name``) whose text joins the two columns."""
    by_rules: bool = True
    """Whether the rules (a name or a JOIN) found the pair."""
    ai: AiGuess | None = None
    """The AI's guess for the pair (in either direction), when it found it."""
    counts: Counts | None = None
    count_status: str = "no_connection"
    """One of ``COUNT_STATUSES``."""
    count_reason: str | None = None
    """Why a count failed (a safe message)."""

    @property
    def rule_score(self) -> float | None:
        """The rule score; ``None`` when the rules did not find the pair."""
        if not self.by_rules:
            return None
        return score(name=self.name, type_=self.type_, unique=self.unique, joined=bool(self.joins))

    @property
    def found_by(self) -> str:
        """``rules``, ``ai`` or ``both``."""
        if self.by_rules and self.ai is not None:
            return "both"
        return "rules" if self.by_rules else "ai"

    def guess(self, *, counted: bool) -> float:
        return guess_score(
            self.rule_score, self.ai.confidence if self.ai else None, counted=counted
        )

    @property
    def confidence(self) -> float:
        if self.count_status == "counted" and self.counts is not None:
            return counted_confidence(self.guess(counted=True), self.counts)
        if self.count_status == "timed_out":
            return timed_out_confidence(self.guess(counted=False))
        return self.guess(counted=False)

    def best_case(self) -> float:
        """The confidence if the counts turned out complete: whether the pair can reach a
        threshold at all."""
        return max(
            self.guess(counted=False),
            COUNT_WEIGHT + (1 - COUNT_WEIGHT) * self.guess(counted=True),
        )

    @property
    def origin(self) -> str:
        if self.joins:
            return "routine"
        return "inferred" if self.by_rules else "ai"

    def reversed(self) -> Candidate:
        """The same pair the other way round, not yet counted."""
        return Candidate(
            from_col=self.to_col,
            to_col=self.from_col,
            name=name_similarity(self.to_col.name, self.from_col.table, self.from_col.name),
            type_=self.type_,
            unique=_unique_strength(self.from_col),
            joins=list(self.joins),
            by_rules=self.by_rules,
            ai=self.ai,
        )

    def evidence(self, threshold: float) -> dict[str, Any]:
        """Why the pair was proposed and how it was counted. Names, scores and counts
        only, never a data value."""
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
        found: dict[str, Any] = {"found_by": self.found_by, "signals": signals}
        if self.rule_score is not None:
            found["rule_score"] = self.rule_score
        if self.ai is not None:
            found["ai_guess"] = {
                "confidence": round(self.ai.confidence, 4),
                "reason": self.ai.reason,
            }
        found["counts"] = self._counts_evidence()
        found["threshold"] = threshold
        return found

    def _counts_evidence(self) -> dict[str, Any]:
        if self.count_status == "counted" and self.counts is not None:
            c = self.counts
            return {
                "status": "counted",
                "target_rows": c.target_rows,
                "target_values": c.target_values,
                "target_distinct": c.target_distinct,
                "target_unique": c.target_unique,
                "child_values": c.child_values,
                "child_distinct": c.child_distinct,
                "matched_rows": c.matched_rows,
                "orphan_rows": c.orphan_rows,
                "match_rate": round(c.match_rate, 4),
            }
        note = self.count_reason if self.count_status == "failed" else None
        return {
            "status": self.count_status,
            "note": note or _COUNT_NOTES.get(self.count_status, "not counted"),
        }


def find_candidates(
    columns: Iterable[Col],
    joins: Mapping[tuple[uuid.UUID, uuid.UUID], list[str]],
    declared: set[frozenset[uuid.UUID]],
    *,
    threshold: float,
) -> list[Candidate]:
    """The rules' pairs: those with a name or JOIN signal that could reach ``threshold``
    once counted. Two columns of one table may pair (a self-reference).

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
            if source.id != target.id:
                propose(source, target, [])

    by_id = {c.id: c for c in cols}
    for (a_id, b_id), objects in joins.items():
        a, b = by_id.get(a_id), by_id.get(b_id)
        if a is None or b is None or a.id == b.id:
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
    return _ordered(found.values())


def union(
    rules: Iterable[Candidate],
    guesses: Iterable[AiGuess],
    *,
    declared: set[frozenset[uuid.UUID]],
    excluded: set[frozenset[uuid.UUID]],
) -> list[Candidate]:
    """The rules' pairs and the AI's, merged on the same two columns in either direction
    (the rules' orientation is kept). ``declared`` (declared foreign keys) and ``excluded``
    (rejected or manual pairs) are dropped either way round, and so is an AI pair whose
    types cannot join. When the AI names a pair twice, its most confident guess is kept."""
    found: dict[frozenset[uuid.UUID], Candidate] = {}
    for candidate in rules:
        key = frozenset((candidate.from_col.id, candidate.to_col.id))
        if key not in declared and key not in excluded:
            found[key] = candidate
    for guess in guesses:
        key = frozenset((guess.from_col.id, guess.to_col.id))
        if guess.from_col.id == guess.to_col.id or key in declared or key in excluded:
            continue
        existing = found.get(key)
        if existing is not None:
            if existing.ai is None or guess.confidence > existing.ai.confidence:
                existing.ai = guess
            continue
        type_ = type_match(guess.from_col.data_type, guess.to_col.data_type)
        if type_ == 0.0:
            continue
        found[key] = Candidate(
            from_col=guess.from_col,
            to_col=guess.to_col,
            name=name_similarity(guess.from_col.name, guess.to_col.table, guess.to_col.name),
            type_=type_,
            unique=_unique_strength(guess.to_col),
            by_rules=False,
            ai=guess,
        )
    return _ordered(found.values())


def count_order(candidates: Iterable[Candidate]) -> list[Candidate]:
    """The order the pairs are counted in: most promising first (found by both before
    one guesser, then by the guess)."""
    return sorted(
        candidates,
        key=lambda c: (c.found_by != "both", -c.guess(counted=False), c.from_col.qualified),
    )


def _ordered(candidates: Iterable[Candidate]) -> list[Candidate]:
    return sorted(
        candidates, key=lambda c: (-c.confidence, c.from_col.qualified, c.to_col.qualified)
    )


def _unique_strength(target: Col) -> float:
    if target.unique_via is None:
        return 0.0
    return 0.8 if target.unique_via.startswith("profiled") else 1.0


def _rank(candidate: Candidate) -> tuple[float, bool, str]:
    return (candidate.confidence, bool(candidate.joins), candidate.from_col.qualified)


# -- the count query ------------------------------------------------------------------

COUNT_OUTPUTS = (
    "target_rows",
    "target_values",
    "target_distinct",
    "child_values",
    "child_distinct",
    "matched_rows",
)


def count_sql(child: Col, parent: Col, dialect: str) -> str:
    """The one read-only query that counts a pair on the source, in ``dialect``: the
    target's rows, non-null and distinct values, and over ``child LEFT JOIN parent`` the
    child's non-null and distinct values and the rows that match. It returns counts only;
    the two columns appear only inside ``COUNT`` and the join condition, all a protected
    column may take part in (spec §6.8). Names are quoted as the Snapshot has them;
    nothing in the text comes from a model or a user. The aliases are long enough not to
    read as a schema name to the Connectors' scope checks."""

    def table(col: Col, alias: str) -> exp.Table:
        return exp.Table(
            this=exp.to_identifier(col.table, quoted=True),
            db=exp.to_identifier(col.schema, quoted=True),
            alias=exp.TableAlias(this=exp.to_identifier(alias)),
        )

    def column(col: Col, alias: str) -> exp.Column:
        return exp.Column(
            this=exp.to_identifier(col.name, quoted=True), table=exp.to_identifier(alias)
        )

    def distinct(col: Col, alias: str) -> exp.Count:
        return exp.Count(this=exp.Distinct(expressions=[column(col, alias)]))

    target = (
        exp.select(
            exp.alias_(exp.Count(this=exp.Star()), "target_rows"),
            exp.alias_(exp.Count(this=column(parent, "dawam_p")), "target_values"),
            exp.alias_(distinct(parent, "dawam_p"), "target_distinct"),
        )
        .from_(table(parent, "dawam_p"))
        .subquery("dawam_t")
    )
    matched = (
        exp.select(
            exp.alias_(exp.Count(this=column(child, "dawam_c")), "child_values"),
            exp.alias_(distinct(child, "dawam_c"), "child_distinct"),
            exp.alias_(exp.Count(this=column(parent, "dawam_m")), "matched_rows"),
        )
        .from_(table(child, "dawam_c"))
        .join(
            table(parent, "dawam_m"),
            on=exp.EQ(this=column(child, "dawam_c"), expression=column(parent, "dawam_m")),
            join_type="left",
        )
        .subquery("dawam_x")
    )
    query = (
        exp.select(*(exp.column(name, table="dawam_t") for name in COUNT_OUTPUTS[:3]))
        .select(*(exp.column(name, table="dawam_x") for name in COUNT_OUTPUTS[3:]))
        .from_(target)
        .join(matched, join_type="cross")
    )
    return query.sql(dialect=dialect)
