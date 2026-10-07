"""The AI source query guard (ADR 0002, spec §6.8, story 68).

One call: ``check_query(sql, engine=, catalog=)``. It either rejects the SQL with a reason or
returns a ``SafeQuery``: SQL that was *regenerated from the verified parse tree* (so nothing the
parser dropped, such as a MySQL ``/*! ... */`` comment, can reach the source) with every table
fully qualified and aliased, plus which output columns the caller must mask.

The guard fails closed. It is a whitelist over the sqlglot tree, not a blacklist over text:

- exactly one statement, and it is a ``SELECT`` (or a set operation of ``SELECT``s);
- only node types the guard understands, and only functions on a per-engine allow-list of
  side-effect-free ones; ``INTO``, locking clauses, hints, ``LATERAL``, table functions, named
  windows and anything else not listed are rejected;
- every table resolves against the Snapshot (``GuardCatalog``) to one table or view in an allowed
  Database Schema of the same database. Synonyms, other databases and linked servers do not resolve;
- every column resolves to exactly one source column; ``SELECT *`` is expanded first;
- views are traced through their stored definition; a view that cannot be traced is not queryable;
- Protected Columns: ``COUNT``, ``COUNT(DISTINCT)`` and column-to-column equality in ``JOIN ON``/
  ``WHERE`` are allowed. Projecting one by name, ``GROUP BY``, ``ORDER BY``, window
  ``PARTITION BY``/``ORDER BY``, value-emitting aggregates (``MIN``, ``MAX``, ``STRING_AGG``...),
  comparing to anything but another column, and joining to a literal-derived value are rejected.
  An output derived from a protected column any other way (``SUBSTR``, ``CONCAT``, ``CAST``,
  ``SUM``, a view column, ``SELECT *``) is returned as masked.

A protected value may appear only in: ``COUNT(col)``, ``COUNT(DISTINCT col)``, ``col IS [NOT] NULL``
and ``col = other_col`` / ``col IN (SELECT other_col ...)`` as top-level ``AND`` conjuncts, and, in
the select list, inside UPPER, LOWER, TRIM, SUBSTR, CONCAT, ``||``, CAST to text, SUM and AVG.
Arithmetic, CASE, other functions and boolean contexts are rejected (they would be an error or
boolean oracle). A column equated with a protected one holds its values, so it is tainted for the
whole query, views and derived tables included: returned masked, never compared to a value. The
regenerated SQL is analysed a second time and must come out identical.

Any unexpected error is a rejection too.

The source database's error text can echo values (a failed CAST, say). The caller must scrub it
before anything reaches the model; that is the query executor's job, not the guard's.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import sqlglot
from sqlglot import exp

MAX_SQL_LENGTH = 20_000
MAX_NODES = 20_000
MAX_VIEW_DEPTH = 10

_DIALECTS = {"postgresql": "postgres", "sqlserver": "tsql", "mysql": "mysql", "oracle": "oracle"}
_CASE_INSENSITIVE = ("sqlserver", "mysql")

# --- the function allow-lists -------------------------------------------------------------------
# Typed functions are named by their sqlglot class key (``exp.Substring.key``); sqlglot maps
# ``SUBSTR``, ``IFNULL``, ``NVL``, ``ISNULL``... onto these. Functions sqlglot does not model are
# ``Anonymous`` and are allowed only by the explicit per-engine name lists below.


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_COMMON_FUNCTIONS = _words(
    "count sum avg min max abs round ceil floor sqrt pow sign trunc coalesce nullif length lower "
    "upper trim substring concat concatws replace left right strposition extract currentdate "
    "currenttimestamp least greatest cast trycast case if initcap reverse stddev variance "
    "rownumber rank denserank ntile lag lead firstvalue lastvalue groupconcat arrayagg "
    "tsordstodate tsordstotimestamp timestrtotime md5 ascii chr translate regexpreplace "
    "regexplike datediff dateadd"
)
_TYPED_FUNCTIONS = {
    "postgresql": _COMMON_FUNCTIONS | _words("timestamptrunc timetostr strtodate splitpart"),
    "sqlserver": _COMMON_FUNCTIONS | _words("convert numbertostr year month day lastday stuff"),
    "mysql": _COMMON_FUNCTIONS | _words("timetostr strtodate year month day timestampdiff"),
    "oracle": _COMMON_FUNCTIONS
    | _words("tochar strtodate nvl2 decodecase systimestamp addmonths monthsbetween tonumber"),
}
_ANONYMOUS_FUNCTIONS = {
    "postgresql": _words("age"),
    "sqlserver": _words("datalength getutcdate isnumeric"),
    "mysql": _words("now"),
    "oracle": _words("trunc"),
}

# Aggregates and window functions that hand a column's values back, not a number about them.
_VALUE_EMITTING = _words(
    "min max least greatest groupconcat arrayagg firstvalue lastvalue lag lead"
)
_REGEX_COMPARISONS = _words("regexplike")


def _classes(names: str) -> tuple[type[exp.Expression], ...]:
    return tuple(getattr(exp, n) for n in names.split() if hasattr(exp, n))


_COMPARISONS = _classes(
    "NEQ GT GTE LT LTE Like ILike SimilarTo NullSafeEQ NullSafeNEQ Between Glob RegexpLike "
    "RegexpILike"
)
# Nodes whose meaning is only their children's: lineage is the union of the children's.
_PLAIN_NODES = _classes(
    "Add Sub Mul Div IntDiv Mod DPipe Neg Escape Distinct Interval Var DataType DataTypeParam "
    "Ordered Order"
)
_FORBIDDEN = _classes(
    "Into Lock Command Insert Update Delete Merge Create Drop Alter Set Hint Pragma Parameter "
    "SessionParameter Placeholder"
)
_MASKING_ARGS = {
    "substring": ("this",),
    "trim": ("this",),
    "upper": ("this",),
    "lower": ("this",),
    "concat": ("this", "expressions"),
    "concatws": ("this", "expressions"),
    "sum": ("this",),
    "avg": ("this",),
    "cast": ("this",),
    "trycast": ("this",),
}
_TEXT_TYPES = {
    getattr(exp.DType, n)
    for n in _words("CHAR VARCHAR NCHAR NVARCHAR TEXT NAME BPCHAR")
    if hasattr(exp.DType, n)
}
_BAD_TYPES = {exp.DType.USERDEFINED, exp.DType.UNKNOWN}
_SAFE_VAR = re.compile(r"^[A-Za-z_]{1,32}$")

_SELECT_ARGS = _words(
    "expressions from_ joins where group having order limit offset distinct with_"
)
_VIEW_SELECT_ARGS = _SELECT_ARGS | {"locks"}
_SETOP_ARGS = _words("this expression distinct with_ order limit offset")


class _Reject(Exception):
    """The SQL (or a view it needs) is not provably safe."""


class _Untraceable(_Reject):
    """A view whose definition the guard cannot follow."""


# --- the interface ------------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardColumn:
    name: str
    protected: bool = False
    """A Protected Column: the one policy of ``internal.pii.is_protected``."""


@dataclass(frozen=True)
class GuardTable:
    schema: str
    name: str
    columns: tuple[GuardColumn, ...]
    kind: str = "table"
    """``table`` or ``view``. Anything else (a synonym, say) is never queryable."""
    definition: str | None = None
    """A view's SQL, as stored in the Snapshot."""


@dataclass(frozen=True)
class GuardCatalog:
    """The latest Snapshot, as far as the guard needs it."""

    database: str
    allowed_schemas: tuple[str, ...]
    tables: tuple[GuardTable, ...]


@dataclass(frozen=True)
class OutputColumn:
    name: str
    masked: bool


@dataclass(frozen=True)
class SafeQuery:
    sql: str
    """Fully qualified and aliased, regenerated from the verified tree. Run this, not the input."""
    columns: tuple[OutputColumn, ...]
    """In result order (names can repeat, so mask by position)."""

    @property
    def masked_ordinals(self) -> tuple[int, ...]:
        return tuple(i for i, c in enumerate(self.columns) if c.masked)

    @property
    def masked_columns(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.masked)


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class UntraceableView:
    schema: str
    name: str
    reason: str


def check_query(sql: str, *, engine: str, catalog: GuardCatalog) -> SafeQuery | Rejected:
    """Reject ``sql`` or return the safe query and the columns to mask. Never raises."""
    try:
        return _Analysis(engine, catalog).check(sql)
    except _Reject as e:
        return Rejected(str(e))
    except Exception:
        return Rejected("The query could not be analysed safely.")


def untraceable_views(*, engine: str, catalog: GuardCatalog) -> tuple[UntraceableView, ...]:
    """Views in the allowed schemas the guard cannot trace (not queryable; shown as untraceable)."""
    try:
        analysis = _Analysis(engine, catalog)
    except _Reject:
        return ()
    found: list[UntraceableView] = []
    for table in analysis.pool:
        if table.kind != "view":
            continue
        try:
            analysis.trace_view(table)
        except _Reject as e:
            found.append(UntraceableView(table.schema, table.name, str(e)))
        except Exception:
            found.append(
                UntraceableView(table.schema, table.name, "The view could not be analysed.")
            )
    return tuple(found)


# --- internals ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Col:
    name: str
    prot: frozenset[str] = frozenset()
    """The Protected Columns (``schema.table.column``) this value derives from."""
    identity: bool = False
    """The value is a protected column's value unchanged (a bare reference to it)."""
    literal: bool = False
    """The expression contains a literal, so it can smuggle a constant into a comparison."""
    from_star: bool = False
    src: frozenset[str] = frozenset()
    """Every base column (``schema.table.column``) the value derives from."""


@dataclass(frozen=True)
class _Info:
    prot: frozenset[str] = frozenset()
    identity: bool = False
    literal: bool = False
    name: str | None = None
    """Set for a bare column reference: the resolved column's name."""
    src: frozenset[str] = frozenset()


def _merge(infos: Iterable[_Info]) -> _Info:
    prot: frozenset[str] = frozenset()
    src: frozenset[str] = frozenset()
    literal = False
    for i in infos:
        prot |= i.prot
        src |= i.src
        literal = literal or i.literal
    return _Info(prot=prot, literal=literal, src=src)


def _derive(infos: Iterable[_Info]) -> _Info:
    """The merged lineage of a computed value; with no column behind it, it is a constant."""
    m = _merge(infos)
    return replace(m, literal=m.literal or not m.src)


def _const(info: _Info) -> bool:
    """Not derived from a base-table column (a literal, a count, a row number): it can pin a
    protected column to a value when equated with it."""
    return info.literal or not info.src


@dataclass
class _Source:
    key: str | None
    out: str
    cols: list[_Col]


@dataclass
class _Scope:
    parent: _Scope | None = None
    sources: list[_Source] = field(default_factory=list)


def _is_int(node: object) -> bool:
    return isinstance(node, exp.Literal) and not node.is_string and node.name.isdigit()


def _plain_column(node: exp.Expression, info: _Info) -> bool:
    """A bare column reference whose value, if protected, is the protected value unchanged."""
    return isinstance(_unparen(node), exp.Column) and (not info.prot or info.identity)


def _unparen(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


class _Analysis:
    def __init__(self, engine: str, catalog: GuardCatalog) -> None:
        if engine not in _DIALECTS:
            raise _Reject("The source engine is not supported.")
        self.engine = engine
        self.dialect = _DIALECTS[engine]
        self.ci = engine in _CASE_INSENSITIVE
        self.catalog = catalog
        self.allowed = tuple(catalog.allowed_schemas)
        self.pool = tuple(t for t in catalog.tables if self.name_in(t.schema, self.allowed))
        self.typed_functions = _TYPED_FUNCTIONS[engine]
        self.anonymous_functions = _ANONYMOUS_FUNCTIONS[engine]
        self.prot_keys = frozenset(
            f"{t.schema}.{t.name}.{c.name}" for t in self.pool for c in t.columns if c.protected
        )
        self.view_state: dict[tuple[str, str], tuple[frozenset[str], list[frozenset[str]]]] = {}
        self._views: dict[tuple[str, str], list[_Col] | _Reject] = {}
        self._tracing: list[tuple[str, str]] = []

    # names -----------------------------------------------------------------------------------

    def key(self, name: str) -> str:
        return name.casefold() if self.ci else name

    def name_in(self, name: str, names: Iterable[str]) -> bool:
        k = self.key(name)
        return any(self.key(n) == k for n in names)

    def fold(self, ident: exp.Expression) -> str:
        """What an identifier names: quoted is exact, unquoted folds as the engine does."""
        if not isinstance(ident, exp.Identifier):
            raise _Reject("Unsupported name syntax.")
        name = ident.name
        if ident.args.get("quoted"):
            return name
        if self.engine == "postgresql":
            return name.lower()
        if self.engine == "oracle":
            return name.upper()
        return name

    # tables ----------------------------------------------------------------------------------

    def resolve_table(
        self,
        name: exp.Expression,
        db: exp.Expression | None,
        catalog: exp.Expression | None,
        default_schema: str | None,
    ) -> GuardTable:
        if catalog is not None:
            if self.engine not in ("postgresql", "sqlserver") or db is None:
                raise _Reject("Names in another database are not allowed; use schema.table.")
            if self.key(self.fold(catalog)) != self.key(self.catalog.database):
                raise _Reject("Tables of other databases or linked servers are not allowed.")
        table_name = self.fold(name)
        schema = self.fold(db) if db is not None else None
        if schema is not None and not self.name_in(schema, self.allowed):
            raise _Reject(f"The schema {schema!r} is not an allowed Database Schema.")
        matches = [
            t
            for t in self.pool
            if self.key(t.name) == self.key(table_name)
            and (schema is None or self.key(t.schema) == self.key(schema))
        ]
        if schema is None and default_schema is not None:
            own = [t for t in matches if self.key(t.schema) == self.key(default_schema)]
            matches = own or matches
        if not matches:
            raise _Reject(
                f"The table {table_name!r} is not in the latest Snapshot's allowed schemas."
            )
        if len(matches) > 1:
            raise _Reject(
                f"The table name {table_name!r} is ambiguous; qualify it as schema.table."
            )
        table = matches[0]
        if table.kind not in ("table", "view"):
            raise _Reject(
                f"{table.schema}.{table.name} is a {table.kind}, which cannot be queried."
            )
        return table

    def table_columns(self, table: GuardTable) -> list[_Col]:
        if table.kind == "view":
            try:
                return list(self.trace_view(table))
            except _Untraceable as e:
                raise _Reject(str(e)) from e
        return [self.base_col(table, c) for c in table.columns]

    @staticmethod
    def base_col(table: GuardTable, c: GuardColumn) -> _Col:
        if c.protected:
            key = f"{table.schema}.{table.name}.{c.name}"
            return _Col(c.name, frozenset({key}), identity=True, src=frozenset({key}))
        return _Col(c.name, src=frozenset({f"{table.schema}.{table.name}.{c.name}"}))

    def trace_view(self, table: GuardTable) -> list[_Col]:
        key = (table.schema, table.name)
        cached = self._views.get(key)
        if isinstance(cached, _Reject):
            raise cached
        if cached is not None:
            return cached
        label = f"{table.schema}.{table.name}"
        if key in self._tracing or len(self._tracing) >= MAX_VIEW_DEPTH:
            err = _Untraceable(
                f"The view {label} is untraceable: it is circular or nested too deeply."
            )
            self._views[key] = err
            raise err
        self._tracing.append(key)
        try:
            cols = self._trace(table, label)
        except _Reject as e:
            err = (
                e
                if isinstance(e, _Untraceable)
                else _Untraceable(f"The view {label} is untraceable: {e}")
            )
            self._views[key] = err
            raise err from e
        except Exception as e:
            err = _Untraceable(
                f"The view {label} is untraceable: its definition could not be analysed."
            )
            self._views[key] = err
            raise err from e
        finally:
            self._tracing.pop()
        self._views[key] = cols
        return cols

    def _trace(self, table: GuardTable, label: str) -> list[_Col]:
        if not table.definition or len(table.definition) > MAX_SQL_LENGTH * 5:
            raise _Reject("it has no usable definition")
        try:
            parsed = sqlglot.parse(table.definition, read=self.dialect)
        except Exception as e:
            raise _Reject("its definition does not parse") from e
        if len(parsed) != 1 or parsed[0] is None:
            raise _Reject("its definition is not a single statement")
        tree = parsed[0]
        if isinstance(tree, exp.Create) and str(tree.args.get("kind")).upper() == "VIEW":
            tree = tree.args.get("expression")
        if tree is None:
            raise _Reject("its definition is not a query")
        resolver = _Resolver(self, strict=False, default_schema=table.schema)
        cols = resolver.query(tree, None)
        if len(cols) != len(table.columns):
            raise _Reject("its columns do not match its definition")
        self.view_state[(table.schema, table.name)] = (frozenset(resolver.reveal), resolver.edges)
        out = []
        for traced, declared in zip(cols, table.columns, strict=True):
            prot, identity, src = traced.prot, traced.identity, traced.src
            if declared.protected:
                own = f"{table.schema}.{table.name}.{declared.name}"
                prot, identity, src = prot | {own}, True, src | {own}
            out.append(_Col(declared.name, prot, identity, traced.literal, src=src))
        return out

    # the query -------------------------------------------------------------------------------

    def check(self, sql: str) -> SafeQuery:
        first = self._run(sql, verify=False)
        # the SQL we hand out is analysed again, from scratch, and must match
        second = self._run(first.sql, verify=True)
        if second.sql != first.sql or second.columns != first.columns:
            raise _Reject("The query could not be normalised safely.")
        return first

    def _run(self, sql: str, *, verify: bool) -> SafeQuery:
        if not isinstance(sql, str) or not sql.strip():
            raise _Reject("The query is empty.")
        if len(sql) > MAX_SQL_LENGTH:
            raise _Reject("The query is too long.")
        if "\x00" in sql:
            raise _Reject("The query contains a NUL character.")
        if "/*!" in sql or "/*+" in sql:
            raise _Reject("Executable comments and optimizer hints are not allowed.")
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except Exception as e:
            raise _Reject("The query could not be parsed.") from e
        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            raise _Reject("Exactly one SELECT statement is allowed.")
        tree = statements[0]
        resolver = _Resolver(self, strict=True, default_schema=None)
        cols = resolver.query(tree, None)
        tainted = resolver.finish()
        for c in cols:
            if c.identity and not c.from_star and not verify:
                raise _Reject(
                    f"The query returns the protected column {sorted(c.prot)[0]} as is. "
                    "Protected columns can be counted and joined on, not projected."
                )
        out_sql = tree.sql(dialect=self.dialect, identify=True, comments=False)
        self._verify_output(out_sql, resolver)
        masked = tuple(OutputColumn(c.name, bool(c.prot or c.src & tainted)) for c in cols)
        return SafeQuery(out_sql, masked)

    def _verify_output(self, sql: str, resolver: _Resolver) -> None:
        """Re-parse what we are about to hand out and check it independently of the analysis."""
        again = sqlglot.parse(sql, read=self.dialect)
        if len(again) != 1 or not isinstance(again[0], exp.Query):
            raise _Reject("The query could not be normalised safely.")
        forbidden = _FORBIDDEN
        for node in again[0].walk():
            if node.comments or isinstance(node, forbidden):
                raise _Reject("The query could not be normalised safely.")
            if isinstance(node, exp.Table):
                db, name = node.args.get("db"), node.this
                if db is None:
                    if name.name not in resolver.cte_names:
                        raise _Reject("The query could not be normalised safely.")
                elif (db.name, name.name) not in resolver.tables_used or node.args.get("catalog"):
                    raise _Reject("The query could not be normalised safely.")
            is_operator = isinstance(
                node, (exp.And, exp.Or, exp.Exists)
            )  # sqlglot models these as Funcs
            if isinstance(node, exp.Func) and not is_operator and not self.function_allowed(node):
                raise _Reject("The query could not be normalised safely.")

    def function_allowed(self, node: exp.Func) -> bool:
        if isinstance(node, exp.Anonymous):
            return node.name.lower() in self.anonymous_functions
        return node.key in self.typed_functions


class _Resolver:
    """Resolves one query (and its subqueries) against the catalog, rewriting the tree in place."""

    def __init__(self, analysis: _Analysis, *, strict: bool, default_schema: str | None) -> None:
        self.a = analysis
        self.strict = strict
        self.default_schema = default_schema
        self.ctes: list[dict[str, tuple[str, list[_Col]]]] = []
        self.cte_names: set[str] = set()
        self.tables_used: set[tuple[str, str]] = set()
        self.reveal: set[str] = set()
        """Base columns whose values a query condition, ordering, grouping or error could reveal."""
        self.edges: list[frozenset[str]] = []
        """Base columns equated with each other by a join, filter or IN."""
        self._n = 0
        self._budget = MAX_NODES

    # helpers ---------------------------------------------------------------------------------

    def tick(self) -> None:
        self._budget -= 1
        if self._budget < 0:
            raise _Reject("The query is too complex.")

    def alias(self) -> str:
        self._n += 1
        return f"t{self._n}"

    @staticmethod
    def ident(name: str) -> exp.Identifier:
        return exp.Identifier(this=name, quoted=True)

    def need(self, node: exp.Expression, allowed: Iterable[str]) -> None:
        """Reject any argument of ``node`` that is set but not one the guard handles."""
        allowed = set(allowed)
        for k, v in node.args.items():
            if k in allowed:
                continue
            if v is None or v is False or v == [] or v == "":
                continue
            raise _Reject(f"Unsupported SQL ({type(node).__name__} {k}).")

    # queries ---------------------------------------------------------------------------------

    def query(self, node: exp.Expression, outer: _Scope | None) -> list[_Col]:
        self.tick()
        while isinstance(node, exp.Subquery):
            self.need(node, ("this",))
            node = node.this
        if isinstance(node, exp.SetOperation):
            return self.set_operation(node, outer)
        if isinstance(node, exp.Select):
            return self.select(node, outer)
        raise _Reject("Only a single SELECT query is allowed.")

    def with_clause(self, node: exp.Expression) -> bool:
        clause = node.args.get("with_")
        if not clause:
            return False
        self.need(clause, ("expressions",))
        if clause.args.get("recursive"):
            raise _Reject("Recursive CTEs are not allowed.")
        frame: dict[str, tuple[str, list[_Col]]] = {}
        self.ctes.append(frame)
        for cte in clause.expressions:
            self.need(cte, ("this", "alias"))
            alias = cte.args.get("alias")
            if (
                alias is None
                or alias.args.get("columns")
                or not isinstance(alias.this, exp.Identifier)
            ):
                raise _Reject("A CTE needs a plain name.")
            key = self.a.key(self.a.fold(alias.this))
            if key in frame:
                raise _Reject("Duplicate CTE name.")
            cols = self.query(cte.this, None)
            out = self.alias()
            self.cte_names.add(out)
            alias.set("this", self.ident(out))
            frame[key] = (out, cols)
        return True

    def set_operation(self, node: exp.SetOperation, outer: _Scope | None) -> list[_Col]:
        pushed = self.with_clause(node)
        try:
            self.need(node, _SETOP_ARGS)
            if node.args.get("by_name") or node.args.get("side") or node.args.get("kind"):
                raise _Reject("Unsupported set operation.")
            left = self.query(node.this, outer)
            right = self.query(node.expression, outer)
            if len(left) != len(right):
                raise _Reject("The set operation's queries have different column counts.")
            cols = [
                _Col(
                    lc.name,
                    lc.prot | rc.prot,
                    lc.identity or rc.identity,
                    lc.literal or rc.literal,
                    lc.from_star and rc.from_star,
                    lc.src | rc.src,
                )
                for lc, rc in zip(left, right, strict=True)
            ]
            if not (isinstance(node, exp.Union) and not node.args.get("distinct")):
                self.rows_compared(cols, type(node).__name__.upper())
            self.tail(node, _Scope(parent=outer), cols)
            return cols
        finally:
            if pushed:
                self.ctes.pop()

    def select(self, node: exp.Select, outer: _Scope | None) -> list[_Col]:
        pushed = self.with_clause(node)
        try:
            self.need(node, _SELECT_ARGS if self.strict else _VIEW_SELECT_ARGS)
            distinct = node.args.get("distinct")
            if distinct is not None and (
                distinct.args.get("on") or distinct.args.get("expressions")
            ):
                raise _Reject("DISTINCT ON is not allowed.")
            scope = _Scope(parent=outer)
            from_ = node.args.get("from_")
            joins = list(node.args.get("joins") or [])
            if from_ is not None:
                self.add_source(from_.this, scope, outer)
            for j in joins:
                self.check_join(j)
                self.add_source(j.this, scope, outer)
            for j in joins:
                on = j.args.get("on")
                if on is not None:
                    self.root(on, scope)
            cols = self.projections(node, scope)
            if distinct is not None:
                self.rows_compared(cols, "SELECT DISTINCT")
            where = node.args.get("where")
            if where is not None:
                self.root(where.this, scope)
            having = node.args.get("having")
            if having is not None:
                self.root(having.this, scope)
            self.group(node, scope, cols)
            self.tail(node, scope, cols)
            return cols
        finally:
            if pushed:
                self.ctes.pop()

    def rows_compared(self, cols: list[_Col], what: str) -> None:
        """DISTINCT, UNION, INTERSECT and EXCEPT compare whole rows, which would test a protected
        value against whatever the other side holds (a literal, say)."""
        if self.strict:
            self.no_protected([_Info(prot=c.prot) for c in cols], f"used in {what}")

    def check_join(self, join: exp.Join) -> None:
        self.need(join, ("this", "on", "side", "kind"))
        if join.args.get("method") or join.args.get("using"):
            raise _Reject("NATURAL and USING joins are not allowed; write the ON condition.")
        if str(join.args.get("kind") or "").upper() not in ("", "INNER", "OUTER", "CROSS"):
            raise _Reject("Unsupported join type.")
        if str(join.args.get("side") or "").upper() not in ("", "LEFT", "RIGHT", "FULL"):
            raise _Reject("Unsupported join type.")

    # sources ---------------------------------------------------------------------------------

    def add_source(self, node: exp.Expression, scope: _Scope, outer: _Scope | None) -> None:
        self.tick()
        if isinstance(node, exp.Subquery) and not self.strict and node.args.get("alias") is None:
            inner = node.this  # a parenthesised join, as view definitions are stored
            while isinstance(inner, exp.Subquery) and inner.args.get("alias") is None:
                inner = inner.this
            if isinstance(inner, exp.Table):
                node = inner
        if isinstance(node, exp.Table):
            nested = node.args.get("joins")
            if nested:  # a parenthesised join, as view definitions are stored: flatten it
                if self.strict:
                    raise _Reject("Unsupported SQL (Table joins).")
                node.set("joins", None)
                for j in nested:
                    self.check_join(j)
                self.table_source(node, scope)
                for j in nested:
                    self.add_source(j.this, scope, outer)
                    if j.args.get("on") is not None:
                        self.root(j.args["on"], scope)
                return
            self.table_source(node, scope)
        elif isinstance(node, exp.Subquery):
            self.need(node, ("this", "alias"))
            alias = node.args.get("alias")
            if alias is not None and (
                alias.args.get("columns") or not isinstance(alias.this, exp.Identifier)
            ):
                raise _Reject("A derived table needs a plain alias.")
            cols = self.query(node.this, outer)
            out = self.alias()
            key = None if alias is None else self.a.key(self.a.fold(alias.this))
            node.set("alias", exp.TableAlias(this=self.ident(out)))
            self.add(scope, _Source(key, out, [replace(c, from_star=False) for c in cols]))
        else:
            raise _Reject("Only tables, views and subqueries are allowed in FROM.")

    def table_source(self, node: exp.Table, scope: _Scope) -> None:
        self.need(node, ("this", "db", "catalog", "alias"))
        name, db, catalog = node.this, node.args.get("db"), node.args.get("catalog")
        if not isinstance(name, exp.Identifier) or (
            db is not None and not isinstance(db, exp.Identifier)
        ):
            raise _Reject("Unsupported table name.")
        alias = node.args.get("alias")
        if alias is not None and (
            alias.args.get("columns") or not isinstance(alias.this, exp.Identifier)
        ):
            raise _Reject("A table alias must be a plain name.")
        out = self.alias()
        if db is None and catalog is None:
            cte = self.find_cte(self.a.key(self.a.fold(name)))
            if cte is not None:
                cte_out, cols = cte
                key = self.a.key(self.a.fold(alias.this if alias is not None else name))
                node.set("this", self.ident(cte_out))
                node.set("alias", exp.TableAlias(this=self.ident(out)))
                self.add(scope, _Source(key, out, [replace(c, from_star=False) for c in cols]))
                return
        table = self.a.resolve_table(name, db, catalog, self.default_schema)
        cols = self.a.table_columns(table)
        if table.kind == "view":
            revealed, edges = self.a.view_state[(table.schema, table.name)]
            self.reveal |= revealed
            self.edges.extend(edges)
        key = self.a.key(self.a.fold(alias.this) if alias is not None else table.name)
        self.tables_used.add((table.schema, table.name))
        node.set("this", self.ident(table.name))
        node.set("db", self.ident(table.schema))
        node.set("catalog", None)
        node.set("alias", exp.TableAlias(this=self.ident(out)))
        self.add(scope, _Source(key, out, cols))

    def find_cte(self, key: str) -> tuple[str, list[_Col]] | None:
        for frame in reversed(self.ctes):
            if key in frame:
                return frame[key]
        return None

    def add(self, scope: _Scope, source: _Source) -> None:
        if source.key is not None and any(s.key == source.key for s in scope.sources):
            raise _Reject("Two tables in one FROM have the same name; alias them differently.")
        scope.sources.append(source)

    # projections -----------------------------------------------------------------------------

    def projections(self, node: exp.Select, scope: _Scope) -> list[_Col]:
        cols: list[_Col] = []
        new: list[exp.Expression] = []
        for p in node.expressions:
            star_of: str | None = None
            is_star = isinstance(p, exp.Star)
            if isinstance(p, exp.Column) and isinstance(p.this, exp.Star):
                is_star, star_of = (
                    True,
                    self.a.fold(p.args["table"]) if p.args.get("table") is not None else None,
                )
                if p.args.get("db") is not None or p.args.get("catalog") is not None:
                    raise _Reject("Qualified stars must use the table alias.")
            if is_star:
                if (isinstance(p, exp.Star) and any(p.args.values())) or not scope.sources:
                    raise _Reject("Unsupported SELECT *.")
                sources = scope.sources
                if star_of is not None:
                    found = [s for s in sources if s.key == self.a.key(star_of)]
                    if not found:
                        raise _Reject("Unknown table in SELECT alias.*.")
                    sources = found
                for s in sources:
                    for c in s.cols:
                        new.append(exp.column(c.name, table=s.out, quoted=True))
                        cols.append(replace(c, from_star=True))
                continue
            alias = None
            inner = p
            if isinstance(p, exp.Alias):
                alias, inner = p.args.get("alias"), p.this
                if not isinstance(alias, exp.Identifier):
                    raise _Reject("Unsupported column alias.")
            info = self.expr(inner, scope, False)
            if alias is not None:
                name = self.a.fold(alias)
                p.set("alias", self.ident(name))
                new.append(p)
            elif info.name is not None and isinstance(inner, exp.Column):
                name = info.name
                new.append(p)
            else:
                name = f"col{len(cols) + 1}"
                new.append(exp.Alias(this=p, alias=self.ident(name)))
            cols.append(_Col(name, info.prot, info.identity, info.literal, src=info.src))
        if not cols:
            raise _Reject("The query selects no columns.")
        node.set("expressions", new)
        return cols

    # GROUP BY, ORDER BY, LIMIT ---------------------------------------------------------------

    def group(self, node: exp.Select, scope: _Scope, cols: list[_Col]) -> None:
        group = node.args.get("group")
        if group is None:
            return
        self.need(group, ("expressions",))
        for e in group.expressions:
            self.ordering_item(e, scope, cols, "GROUP BY", alias_first=False)

    def tail(self, node: exp.Expression, scope: _Scope, cols: list[_Col]) -> None:
        order = node.args.get("order")
        if order is not None:
            self.need(order, ("expressions",))
            for o in order.expressions:
                if not isinstance(o, exp.Ordered):
                    raise _Reject("Unsupported ORDER BY.")
                self.need(o, ("this", "desc", "nulls_first"))
                self.ordering_item(o.this, scope, cols, "ORDER BY", alias_first=True)
        self.limits(node)

    def ordering_item(
        self, e: exp.Expression, scope: _Scope, cols: list[_Col], kind: str, *, alias_first: bool
    ) -> None:
        prot: frozenset[str] = frozenset()
        src: frozenset[str] = frozenset()
        if _is_int(e):
            idx = int(e.name) - 1
            if not 0 <= idx < len(cols):
                raise _Reject(f"{kind} position {e.name} is out of range.")
            prot, src = cols[idx].prot, cols[idx].src
        else:
            done = False
            if (
                isinstance(e, exp.Column)
                and e.args.get("table") is None
                and isinstance(e.this, exp.Identifier)
            ):
                wanted = self.a.key(self.a.fold(e.this))
                hits = [c for c in cols if self.a.key(c.name) == wanted]
                if hits and alias_first:
                    if len(hits) > 1 and len({h.prot for h in hits}) > 1:
                        raise _Reject(f"{kind} name {e.name!r} is ambiguous.")
                    prot, src = hits[0].prot, hits[0].src
                    # engines differ on whether an input column can win over the alias
                    with contextlib.suppress(_Reject):
                        i = self.expr(e.copy(), scope, False)
                        prot, src = prot | i.prot, src | i.src
                    e.set("this", self.ident(hits[0].name))
                    done = True
                elif hits:
                    # GROUP BY prefers the input column in some engines and the alias in others
                    try:
                        i = self.expr(e, scope, False)
                        prot, src = i.prot, i.src
                    except _Reject:
                        pass
                    prot, src = prot | hits[0].prot, src | hits[0].src
                    done = True
            if not done:
                i = self.expr(e, scope, False)
                prot, src = i.prot, i.src
        self.reveal |= src
        if prot and self.strict:
            raise _Reject(f"{kind} on the protected column {sorted(prot)[0]} is not allowed.")

    def limits(self, node: exp.Expression) -> None:
        limit = node.args.get("limit")
        if limit is not None:
            if isinstance(limit, exp.Limit):
                self.need(limit, ("expression", "offset", "limit_options"))
                if not _is_int(limit.args.get("expression")) or (
                    limit.args.get("offset") is not None and not _is_int(limit.args["offset"])
                ):
                    raise _Reject("LIMIT must be a plain integer.")
            elif isinstance(limit, exp.Fetch):
                self.need(limit, ("count", "direction", "limit_options"))
                if not _is_int(limit.args.get("count")):
                    raise _Reject("FETCH must be a plain integer.")
            else:
                raise _Reject("Unsupported row limit.")
            opts = limit.args.get("limit_options")
            if opts is not None and (opts.args.get("percent") or opts.args.get("with_ties")):
                raise _Reject("PERCENT and WITH TIES are not allowed.")
        offset = node.args.get("offset")
        if offset is not None:
            self.need(offset, ("expression",))
            if not _is_int(offset.args.get("expression")):
                raise _Reject("OFFSET must be a plain integer.")

    # expressions -----------------------------------------------------------------------------

    def children(self, node: exp.Expression, scope: _Scope) -> list[_Info]:
        return [info for _, info in self.keyed_children(node, scope)]

    def keyed_children(self, node: exp.Expression, scope: _Scope) -> list[tuple[str, _Info]]:
        out = []
        for key, v in node.args.items():
            for item in v if isinstance(v, list) else [v]:
                if isinstance(item, exp.Expression):
                    out.append((key, self.expr(item, scope, False)))
        return out

    def root(self, node: exp.Expression, scope: _Scope) -> None:
        """A WHERE, HAVING or ON condition: the forms that may touch a protected column return
        no lineage, so anything left over is a bare expression used as a boolean."""
        info = self.expr(node, scope, True)
        if self.strict and info.prot:
            raise _Reject(
                f"The protected column {sorted(info.prot)[0]} cannot be used as a condition. "
                "Only COUNT, COUNT(DISTINCT), IS [NOT] NULL and joins on it are allowed."
            )
        self.reveal |= info.src

    def finish(self) -> frozenset[str]:
        """Columns equated (by join, filter or IN) with a protected column hold its values, so they
        are protected for the whole query: they may be returned only masked, and any use that
        reveals a value (a literal comparison, GROUP BY, ORDER BY, a function that can fail on it)
        is rejected. Returns those columns."""
        parent: dict[str, str] = {}

        def find(k: str) -> str:
            parent.setdefault(k, k)
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        for edge in self.edges:
            keys = sorted(edge)
            for k in keys:
                find(k)
            for k in keys[1:]:
                parent[find(k)] = find(keys[0])
        roots = {find(k) for k in list(parent) if k in self.a.prot_keys}
        tainted = frozenset(k for k in list(parent) if find(k) in roots)
        leaked = (tainted - self.a.prot_keys) & self.reveal
        if self.strict and leaked:
            raise _Reject(
                f"The column {sorted(leaked)[0]} is joined to a protected column, so it holds its "
                "values; it cannot be compared to a value, grouped, ordered or transformed."
            )
        return tainted

    def expr(self, node: exp.Expression, scope: _Scope, predicate: bool) -> _Info:
        """The lineage of ``node``. ``predicate``: a position where the forms that may touch a
        protected column are allowed (a top-level ``AND`` conjunct of ``ON``/``WHERE``/``HAVING``).
        """
        self.tick()
        if isinstance(node, exp.Column):
            return self.column(node, scope)
        if isinstance(node, (exp.Literal, exp.Null, exp.Boolean)):
            if (
                isinstance(node, exp.Literal)
                and node.is_string
                and self.a.engine == "mysql"
                and "\\" in node.name
            ):
                raise _Reject("Backslashes in string literals are not allowed for MySQL.")
            return _Info(literal=True)
        if isinstance(node, exp.Paren):
            inner = self.expr(node.this, scope, predicate)
            return replace(inner, name=None)
        if isinstance(node, exp.And):
            return _merge(
                [
                    self.expr(node.this, scope, predicate),
                    self.expr(node.expression, scope, predicate),
                ]
            )
        if isinstance(node, exp.Or):
            return _merge(
                [self.expr(node.this, scope, False), self.expr(node.expression, scope, False)]
            )
        if isinstance(node, exp.Not):
            return self.expr(
                node.this, scope, predicate and isinstance(_unparen(node.this), exp.Is)
            )
        if isinstance(node, exp.EQ):
            return self.equality(node, scope, predicate)
        if isinstance(node, exp.Is):
            return self.is_null(node, scope, predicate)
        if isinstance(node, exp.In):
            return self.in_(node, scope, predicate)
        if isinstance(node, exp.Exists):
            self.query(node.this, scope)
            return _Info(literal=True)
        if isinstance(node, (exp.Subquery, exp.Select, exp.SetOperation)):
            cols = self.query(node, scope)
            if len(cols) != 1:
                raise _Reject("A subquery used as a value must return one column.")
            c = cols[0]
            return _Info(prot=c.prot, src=c.src, literal=c.literal or not c.src)
        if isinstance(node, exp.Window):
            return self.window(node, scope)
        if isinstance(node, _COMPARISONS) and not isinstance(node, exp.Func):
            infos = self.children(node, scope)
            self.no_protected(infos, "compared to a value")
            return self.revealing(infos)
        if isinstance(node, exp.Func):
            return self.func(node, scope)
        if isinstance(node, exp.DataType):
            if self.strict and (node.this in _BAD_TYPES or not isinstance(node.this, exp.DType)):
                raise _Reject("Unsupported data type.")
            return _Info()
        if isinstance(node, exp.Var):
            if self.strict and not _SAFE_VAR.match(node.name):
                raise _Reject("Unsupported keyword.")
            return _Info()
        if isinstance(node, _PLAIN_NODES) or not self.strict:
            infos = self.children(node, scope)
            if isinstance(node, exp.DPipe):  # concatenation: masked, and cannot fail on a value
                return _derive(infos)
            self.no_protected(
                infos,
                "used in an expression outside UPPER, LOWER, TRIM, SUBSTR, CONCAT, CAST, SUM, AVG",
            )
            return self.revealing(infos)
        raise _Reject(f"Unsupported SQL ({type(node).__name__}).")

    def revealing(self, infos: list[_Info]) -> _Info:
        """The result depends on these values in a way a query can observe (a comparison, an
        error): record them as revealed."""
        out = _derive(infos)
        self.reveal |= out.src
        return out

    def no_protected(self, infos: Iterable[_Info], what: str) -> None:
        if not self.strict:
            return
        for i in infos:
            if i.prot:
                raise _Reject(
                    f"The protected column {sorted(i.prot)[0]} cannot be {what}. "
                    "Only COUNT, COUNT(DISTINCT), IS [NOT] NULL and joins on it are allowed."
                )

    def column(self, node: exp.Column, scope: _Scope) -> _Info:
        self.need(node, ("this", "table"))
        name = node.this
        if not isinstance(name, exp.Identifier):
            raise _Reject("Unsupported column reference.")
        wanted = self.a.key(self.a.fold(name))
        table = node.args.get("table")
        if table is not None:
            if not isinstance(table, exp.Identifier):
                raise _Reject("Unsupported column qualifier.")
            tkey = self.a.key(self.a.fold(table))
            sc: _Scope | None = scope
            src = None
            while sc is not None and src is None:
                src = next((s for s in sc.sources if s.key == tkey), None)
                sc = sc.parent
            if src is None:
                raise _Reject(f"The table or alias {table.name!r} is not in this query.")
            hits = [(src, c) for c in src.cols if self.a.key(c.name) == wanted]
        else:
            hits = []
            sc = scope
            while sc is not None and not hits:
                hits = [(s, c) for s in sc.sources for c in s.cols if self.a.key(c.name) == wanted]
                sc = sc.parent
        if not hits:
            raise _Reject(f"The column {name.name!r} does not exist in the tables of this query.")
        if len(hits) > 1:
            raise _Reject(f"The column {name.name!r} is ambiguous; qualify it with a table alias.")
        src, col = hits[0]
        node.set("this", self.ident(col.name))
        node.set("table", self.ident(src.out))
        return _Info(col.prot, col.identity, col.literal, col.name, col.src)

    def equality(self, node: exp.EQ, scope: _Scope, predicate: bool) -> _Info:
        left, right = self.expr(node.this, scope, False), self.expr(node.expression, scope, False)
        if (
            predicate
            and _plain_column(node.this, left)
            and _plain_column(node.expression, right)
            and not _const(left)
            and not _const(right)
        ):
            self.edges.append(
                left.src | right.src
            )  # a join: each side now holds the other's values
            return _Info()
        self.no_protected([left, right], "compared to a value")
        return self.revealing([left, right])

    def is_null(self, node: exp.Is, scope: _Scope, predicate: bool) -> _Info:
        left, right = self.expr(node.this, scope, False), self.expr(node.expression, scope, False)
        if predicate and _plain_column(node.this, left) and isinstance(node.expression, exp.Null):
            return _Info()
        self.no_protected([left, right], "compared to a value")
        return self.revealing([left, right])

    def in_(self, node: exp.In, scope: _Scope, predicate: bool) -> _Info:
        self.need(node, ("this", "expressions", "query"))
        left = self.expr(node.this, scope, False)
        query = node.args.get("query")
        if query is not None:
            cols = self.query(query, scope)
            if len(cols) != 1:
                raise _Reject("IN (subquery) must return one column.")
            right = _Info(
                prot=cols[0].prot,
                identity=cols[0].identity,
                literal=cols[0].literal,
                src=cols[0].src,
            )
            if (
                predicate
                and _plain_column(node.this, left)
                and (not right.prot or right.identity)
                and not _const(left)
                and not _const(right)
            ):
                self.edges.append(left.src | right.src)
                return _Info()
            self.no_protected([left, right], "compared to a value")
            return self.revealing([left, right])
        items = [self.expr(e, scope, False) for e in node.args.get("expressions") or []]
        self.no_protected([left, *items], "compared to a value")
        return self.revealing([left, *items])

    def window(self, node: exp.Window, scope: _Scope) -> _Info:
        self.need(node, ("this", "partition_by", "order", "spec", "over"))
        info = self.expr(node.this, scope, False)
        parts = [self.expr(e, scope, False) for e in node.args.get("partition_by") or []]
        order = node.args.get("order")
        if order is not None:
            self.need(order, ("expressions",))
            for o in order.expressions:
                self.need(o, ("this", "desc", "nulls_first"))
                parts.append(self.expr(o.this, scope, False))
        self.no_protected(parts, "used to partition or order a window")
        for p in parts:
            self.reveal |= p.src
        return _merge([info, *parts]) if not self.strict else info

    def func(self, node: exp.Func, scope: _Scope) -> _Info:
        a = self.a
        if self.strict and not a.function_allowed(node):
            label = node.name if isinstance(node, exp.Anonymous) else node.key
            raise _Reject(f"The function {label!r} is not on the allow-list for this engine.")
        key = node.key
        if isinstance(node, exp.Count):
            self.need(node, ("this", "expressions", "big_int"))
            arg = node.this
            args: list[exp.Expression] = []
            if isinstance(arg, exp.Distinct):
                self.need(arg, ("expressions",))
                args.extend(arg.expressions)
            elif arg is not None and not isinstance(arg, exp.Star):
                args.append(arg)
            args.extend(node.args.get("expressions") or [])
            for e in args:
                i = self.expr(e, scope, False)
                # COUNT(expr) can leak through NULLs (NULLIF, CASE, Oracle's '' IS NULL): only the
                # column itself, its value unchanged, may be counted
                if not _plain_column(e, i):
                    self.no_protected([i], "counted through an expression")
                    self.reveal |= i.src
            return _Info(literal=True)  # a number about the rows, not a column's values
        keyed = self.keyed_children(node, scope)
        if self.strict:
            self.masking_use(node, keyed)
        infos = [i for _, i in keyed]
        bare_sum = key in ("sum", "avg") and isinstance(_unparen(node.this), exp.Column)
        if bare_sum:
            return _derive(infos)
        return self.revealing(infos)

    def masking_use(self, node: exp.Func, keyed: list[tuple[str, _Info]]) -> None:
        """Protected lineage may flow only into functions whose result is masked and that cannot
        fail on, or answer a question about, the value."""
        if not any(i.prot for _, i in keyed):
            return
        allowed = None if isinstance(node, exp.Anonymous) else _MASKING_ARGS.get(node.key)
        for k, i in keyed:
            if i.prot and (allowed is None or k not in allowed):
                self.no_protected([i], f"passed to {node.key.upper()}")
        if node.key in ("sum", "avg") and not isinstance(_unparen(node.this), exp.Column):
            self.no_protected([i for _, i in keyed], "aggregated through an expression")
        if node.key in ("cast", "trycast"):
            target = node.args.get("to")
            if not isinstance(target, exp.DataType) or target.this not in _TEXT_TYPES:
                self.no_protected([i for _, i in keyed], "cast to a non-text type")
