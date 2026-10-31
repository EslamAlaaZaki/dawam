"""Generate the database ERD (``docs/architecture/database/erd.html``) from the real schema.

The schema is read from a PostgreSQL database migrated to head with DAWAM's Alembic
migrations, so the ERD shows what the migrations build (types, nullability, defaults,
keys, CHECK constraints, indexes, triggers), not what the ORM classes claim. Each
module's ``tables.py`` then adds what PostgreSQL does not hold: table and column
descriptions (the class and attribute docstrings), the owning module and the
app-side (SQLAlchemy) defaults.

Usage, from ``backend/``::

    python tools/generate_erd.py            # starts a throwaway PostgreSQL (Docker)
    python tools/generate_erd.py --check    # exit 1 if the committed ERD is stale

``--database-url URL`` reads an already migrated database instead of starting one.
``tests/test_erd_is_current.py`` fails CI when the committed ERD differs from this
output, so a schema change must regenerate it in the same change.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import itertools
import json
import re
import sys
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
ERD_PATH = REPO_ROOT / "docs" / "architecture" / "database" / "erd.html"
TEMPLATE_PATH = Path(__file__).resolve().parent / "erd_template.html"
DATA_PLACEHOLDER = "/*__ERD_DATA__*/null"

POSTGRES_IMAGE = "pgvector/pgvector:pg16"
"""The same image the test suite runs on (tests/conftest.py)."""

SKIPPED_TABLES = {"alembic_version"}
"""Alembic's bookkeeping table, not part of DAWAM's data model."""

_FK_ACTIONS = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}
_CONSTRAINT_KINDS = {
    "p": "PRIMARY KEY",
    "f": "FOREIGN KEY",
    "u": "UNIQUE",
    "c": "CHECK",
    "x": "EXCLUDE",
}


# --- Reading the migrated database --------------------------------------------------

_TABLES_SQL = """
SELECT c.oid, c.relname, obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT a.attrelid, a.attnum, a.attname,
       format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS expr,
       a.attidentity, a.attgenerated,
       col_description(a.attrelid, a.attnum) AS comment,
       CASE WHEN t.typtype = 'e' THEN t.oid
            WHEN et.typtype = 'e' THEN et.oid END AS enum_oid
FROM pg_attribute a
JOIN pg_type t ON t.oid = a.atttypid
LEFT JOIN pg_type et ON et.oid = t.typelem AND t.typcategory = 'A'
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = ANY(:oids) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum
"""

_ENUMS_SQL = """
SELECT t.oid, t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
GROUP BY t.oid, t.typname
"""

_CONSTRAINTS_SQL = """
SELECT con.conrelid, con.conname, con.contype, con.conkey, con.confrelid, con.confkey,
       con.confupdtype, con.confdeltype, con.condeferrable, con.condeferred,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
WHERE con.conrelid = ANY(:oids) AND con.contype IN ('p', 'f', 'u', 'c', 'x')
ORDER BY con.conrelid, con.contype, con.conname
"""

_INDEXES_SQL = """
SELECT i.indrelid, ic.relname AS name, i.indisunique, am.amname AS method,
       i.indkey::int2[] AS keys, pg_get_expr(i.indpred, i.indrelid) AS predicate,
       pg_get_indexdef(i.indexrelid) AS definition,
       EXISTS (SELECT 1 FROM pg_constraint con
               WHERE con.conindid = i.indexrelid AND con.contype IN ('p', 'u', 'x')
                 AND con.conrelid = i.indrelid) AS backs_constraint
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_am am ON am.oid = ic.relam
WHERE i.indrelid = ANY(:oids)
ORDER BY i.indrelid, ic.relname
"""

_TRIGGERS_SQL = """
SELECT tgrelid, tgname, pg_get_triggerdef(oid) AS definition
FROM pg_trigger WHERE tgrelid = ANY(:oids) AND NOT tgisinternal
ORDER BY tgrelid, tgname
"""


def read_schema(conn: sa.Connection) -> list[dict]:
    """Every table in the ``public`` schema, as plain data, sorted by name."""
    tables_rows = [r for r in conn.execute(sa.text(_TABLES_SQL)) if r.relname not in SKIPPED_TABLES]
    oids = [r.oid for r in tables_rows]
    params = {"oids": oids}
    enums = {r.oid: (r.typname, list(r.labels)) for r in conn.execute(sa.text(_ENUMS_SQL))}

    tables: dict[int, dict] = {}
    names: dict[int, dict[int, str]] = {}
    for r in tables_rows:
        tables[r.oid] = {
            "name": r.relname,
            "comment": r.comment,
            "columns": [],
            "primary_key": [],
            "foreign_keys": [],
            "constraints": [],
            "indexes": [],
            "triggers": [],
        }
        names[r.oid] = {}

    for r in conn.execute(sa.text(_COLUMNS_SQL), params):
        names[r.attrelid][r.attnum] = r.attname
        generated = r.expr if r.attgenerated else None
        enum = enums.get(r.enum_oid) if r.enum_oid else None
        tables[r.attrelid]["columns"].append(
            {
                "name": r.attname,
                "type": r.type,
                "nullable": not r.attnotnull,
                "default": None if r.attgenerated else r.expr,
                "identity": {"a": "ALWAYS", "d": "BY DEFAULT"}.get(r.attidentity),
                "generated": generated,
                "comment": r.comment,
                "enum": {"source": f"enum type {enum[0]}", "values": enum[1]} if enum else None,
            }
        )

    table_name = {oid: t["name"] for oid, t in tables.items()}
    for r in conn.execute(sa.text(_CONSTRAINTS_SQL), params):
        table = tables[r.conrelid]
        columns = [names[r.conrelid][k] for k in r.conkey or []]
        constraint = {
            "name": r.conname,
            "kind": _CONSTRAINT_KINDS[r.contype],
            "columns": columns,
            "definition": r.definition,
        }
        if r.condeferrable:
            constraint["deferrable"] = (
                "INITIALLY DEFERRED" if r.condeferred else "INITIALLY IMMEDIATE"
            )
        table["constraints"].append(constraint)
        if r.contype == "p":
            table["primary_key"] = columns
        elif r.contype == "f":
            table["foreign_keys"].append(
                {
                    "name": r.conname,
                    "columns": columns,
                    "ref_table": table_name.get(r.confrelid, "?"),
                    "ref_columns": [names[r.confrelid][k] for k in r.confkey],
                    "on_update": _FK_ACTIONS[r.confupdtype],
                    "on_delete": _FK_ACTIONS[r.confdeltype],
                }
            )
        elif r.contype == "c":
            for column, values in check_enum_values(r.definition, columns):
                col = next(c for c in table["columns"] if c["name"] == column)
                if col["enum"] is None:
                    col["enum"] = {"source": f"CHECK {r.conname}", "values": values}

    for r in conn.execute(sa.text(_INDEXES_SQL), params):
        table = tables[r.indrelid]
        table["indexes"].append(
            {
                "name": r.name,
                "unique": r.indisunique,
                "method": r.method,
                "columns": [names[r.indrelid][k] if k else "(expression)" for k in r.keys],
                "predicate": r.predicate,
                "definition": r.definition,
                "backs_constraint": r.backs_constraint,
            }
        )

    for r in conn.execute(sa.text(_TRIGGERS_SQL), params):
        tables[r.tgrelid]["triggers"].append({"name": r.tgname, "definition": r.definition})

    return sorted(tables.values(), key=lambda t: t["name"])


_ANY_ARRAY = re.compile(r"\(?\(?(\w+)\)?(?:::[\w ]+)?\s*=\s*ANY\s*\(\(?ARRAY\[(.*?)\]")
_SUBSET_ARRAY = re.compile(r"\(?(\w+)\s*<@\s*\(?ARRAY\[(.*?)\]")
_LITERAL = re.compile(r"'((?:[^']|'')*)'")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def check_enum_values(definition: str, columns: list[str]) -> Iterator[tuple[str, list[str]]]:
    """The allowed values a CHECK constraint lists for a column (``col IN (...)``,
    which PostgreSQL stores as ``col = ANY (ARRAY[...])``, or ``col <@ ARRAY[...]``
    for an array column)."""
    for pattern in (_ANY_ARRAY, _SUBSET_ARRAY):
        for match in pattern.finditer(definition):
            column, items = match.group(1), match.group(2)
            values = [v.replace("''", "'") for v in _LITERAL.findall(items)]
            if not values:  # numbers: ARRAY[0, 1, 2]
                values = [v.strip() for v in items.split(",") if _NUMBER.fullmatch(v.strip())]
            if column in columns and values:
                yield column, values


# --- What the ORM classes add ---------------------------------------------------------


def _attribute_docstrings(cls: type) -> dict[str, str]:
    """``attribute -> docstring`` for the string literal right after each assignment."""
    try:
        source = textwrap.dedent(inspect.getsource(cls))
    except (OSError, TypeError):
        return {}
    class_def = ast.parse(source).body[0]
    docs: dict[str, str] = {}
    body = getattr(class_def, "body", [])
    for statement, following in itertools.pairwise(body):
        if not (
            isinstance(following, ast.Expr)
            and isinstance(following.value, ast.Constant)
            and isinstance(following.value.value, str)
        ):
            continue
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            docs[statement.target.id] = inspect.cleandoc(following.value.value)
        elif isinstance(statement, ast.Assign) and isinstance(statement.targets[0], ast.Name):
            docs[statement.targets[0].id] = inspect.cleandoc(following.value.value)
    return docs


def _app_default(column: sa.Column) -> str | None:
    default = column.default
    if default is None or not hasattr(default, "arg"):
        return None
    if default.is_callable:
        return f"{getattr(default.arg, '__name__', 'callable')}()"
    if default.is_sequence or default.is_clause_element:
        return str(default.arg)
    return repr(default.arg)


def orm_documentation() -> dict[str, dict]:
    """``table name -> {module, orm_class, source, description, columns}`` from the
    ORM classes in each module's ``tables.py``."""
    import dawam.modules  # noqa: F401  (registers every module's tables, as Alembic does)
    from dawam.platform.db import Base

    docs: dict[str, dict] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = mapper.local_table
        parts = cls.__module__.split(".")
        in_modules = parts[:2] == ["dawam", "modules"] and len(parts) > 2
        module = parts[2] if in_modules else cls.__module__
        source = Path(inspect.getsourcefile(cls)).resolve().relative_to(REPO_ROOT).as_posix()
        attribute_docs = _attribute_docstrings(cls)
        columns: dict[str, dict] = {}
        for prop in mapper.column_attrs:
            for column in prop.columns:
                if getattr(column, "table", None) is not table:
                    continue
                columns[column.name] = {
                    "attribute": prop.key,
                    "description": attribute_docs.get(prop.key),
                    "app_default": _app_default(column),
                }
        module_doc = sys.modules[cls.__module__].__doc__
        class_doc = cls.__dict__.get("__doc__")
        docs[table.name] = {
            "module": module,
            "module_description": inspect.cleandoc(module_doc) if module_doc else None,
            "orm_class": cls.__name__,
            "source": source,
            "description": inspect.cleandoc(class_doc) if class_doc else None,
            "columns": columns,
        }
    return docs


# --- Relationships --------------------------------------------------------------------


def _unique_column_sets(table: dict) -> list[set[str]]:
    sets = [set(table["primary_key"])] if table["primary_key"] else []
    sets += [set(c["columns"]) for c in table["constraints"] if c["kind"] == "UNIQUE"]
    sets += [set(i["columns"]) for i in table["indexes"] if i["unique"] and not i["predicate"]]
    return sets


def relationships(tables: list[dict]) -> tuple[list[dict], list[dict]]:
    """Each foreign key as a relationship (``N:1`` or ``1:1``), and the ``N:M``
    relationships junction tables make (a primary key made only of foreign keys to
    two or more tables)."""
    edges = []
    for table in tables:
        unique_sets = _unique_column_sets(table)
        nullable = {c["name"]: c["nullable"] for c in table["columns"]}
        for fk in table["foreign_keys"]:
            one_to_one = set(fk["columns"]) in unique_sets
            edges.append(
                {
                    "name": fk["name"],
                    "from_table": table["name"],
                    "from_columns": fk["columns"],
                    "to_table": fk["ref_table"],
                    "to_columns": fk["ref_columns"],
                    "cardinality": "1:1" if one_to_one else "N:1",
                    "optional": any(nullable[c] for c in fk["columns"]),
                    "on_delete": fk["on_delete"],
                    "on_update": fk["on_update"],
                }
            )

    many_to_many = []
    for table in tables:
        pk = set(table["primary_key"])
        fks = [fk for fk in table["foreign_keys"] if set(fk["columns"]) <= pk]
        covered = set().union(*(fk["columns"] for fk in fks)) if fks else set()
        if len(fks) < 2 or covered != pk:
            continue
        for i, a in enumerate(fks):
            for b in fks[i + 1 :]:
                many_to_many.append(
                    {
                        "via": table["name"],
                        "a_table": a["ref_table"],
                        "a_columns": a["columns"],
                        "b_table": b["ref_table"],
                        "b_columns": b["columns"],
                    }
                )
    return edges, many_to_many


# --- Putting it together --------------------------------------------------------------


def build_erd_data(conn: sa.Connection) -> dict:
    tables = read_schema(conn)
    docs = orm_documentation()
    for table in tables:
        doc = docs.get(table["name"])
        table["in_orm"] = doc is not None
        table["module"] = doc["module"] if doc else "(no ORM class)"
        table["orm_class"] = doc["orm_class"] if doc else None
        table["source"] = doc["source"] if doc else None
        table["description"] = table.pop("comment") or (doc["description"] if doc else None)
        for column in table["columns"]:
            col_doc = (doc or {}).get("columns", {}).get(column["name"])
            column["in_orm"] = col_doc is not None
            column["attribute"] = col_doc["attribute"] if col_doc else None
            column["app_default"] = col_doc["app_default"] if col_doc else None
            orm_description = col_doc["description"] if col_doc else None
            column["description"] = column.pop("comment") or orm_description
            column["pk"] = column["name"] in table["primary_key"]
            column["fk"] = [
                {
                    "table": fk["ref_table"],
                    "column": fk["ref_columns"][fk["columns"].index(column["name"])],
                }
                for fk in table["foreign_keys"]
                if column["name"] in fk["columns"]
            ]

    modules = {}
    for doc in docs.values():
        modules.setdefault(doc["module"], doc["module_description"])
    edges, many_to_many = relationships(tables)
    return {
        "alembic_head": sorted(current_revisions_from(conn)),
        "modules": [{"name": n, "description": modules[n]} for n in sorted(modules)],
        "tables": tables,
        "relationships": edges,
        "many_to_many": many_to_many,
        "orm_tables_missing_from_database": sorted(set(docs) - {t["name"] for t in tables}),
    }


def current_revisions_from(conn: sa.Connection) -> set[str]:
    if not sa.inspect(conn).has_table("alembic_version"):
        return set()
    return set(conn.scalars(sa.text("SELECT version_num FROM alembic_version")))


def render_html(data: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = json.dumps(data, indent=1, ensure_ascii=False)
    # Safe inside <script>: no "</script>" or "<!--" can appear.
    payload = payload.replace("<", "\\u003c")
    if DATA_PLACEHOLDER not in template:
        raise RuntimeError(f"{TEMPLATE_PATH} lacks the {DATA_PLACEHOLDER} placeholder")
    return template.replace(DATA_PLACEHOLDER, payload)


def erd_html_from_database(database_url: str) -> str:
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            return render_html(build_erd_data(conn))
    finally:
        engine.dispose()


def migrated_erd_html(empty_database_url: str) -> str:
    """Migrate an empty database to head, then generate the ERD from it."""
    from dawam.platform.migrations import upgrade_to_head

    engine = sa.create_engine(empty_database_url, poolclass=sa.pool.NullPool)
    try:
        upgrade_to_head(engine)
    finally:
        engine.dispose()
    return erd_html_from_database(empty_database_url)


@contextmanager
def throwaway_database() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        yield container.get_connection_url()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--database-url", help="read this migrated database instead of starting one"
    )
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed ERD is stale")
    parser.add_argument("--output", type=Path, default=ERD_PATH)
    args = parser.parse_args(argv)

    if args.database_url:
        html = erd_html_from_database(args.database_url)
    else:
        with throwaway_database() as url:
            html = migrated_erd_html(url)

    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != html:
            print(f"{args.output} is stale: run python tools/generate_erd.py", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date.")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
