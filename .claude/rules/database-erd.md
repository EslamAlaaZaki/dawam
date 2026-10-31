---
paths:
  - "backend/src/dawam/modules/*/tables.py"
  - "backend/src/dawam/migrations/**"
  - "backend/tools/generate_erd.py"
  - "backend/tools/erd_template.html"
  - "docs/architecture/database/**"
---

# Keeping the database ERD in sync

`docs/architecture/database/erd.html` must match the schema the migrations build. A
schema change is done only when the regenerated ERD is in the same change.

## What counts as a schema change

Any migration that creates, drops or renames a table; adds, changes or removes a
column; changes a type, nullability or default; changes CHECK-listed values or an enum
type; or adds or changes a primary key, foreign key, unique constraint, index, trigger
or relationship. Docstring edits in a `tables.py` count too: class docstrings become
table descriptions and attribute docstrings become column descriptions.

## Steps

1. Make the schema change (migration plus `tables.py`).
2. In `backend/`, run `python tools/generate_erd.py`. It starts a throwaway PostgreSQL
   (`pgvector/pgvector:pg16`, Docker), migrates it to head, reads the catalog and
   rewrites the ERD. `--database-url URL` reads an already migrated database instead.
3. Run `pytest tests/test_erd_is_current.py`: green means the ERD matches.
4. Commit `docs/architecture/database/erd.html` together with the schema change.

## How the ERD is built

- Source of truth is the migrated database (`pg_catalog`): types, nullability,
  defaults, keys, constraints, indexes, triggers. The ORM classes add descriptions,
  the owning module and app-side (SQLAlchemy) defaults.
- Allowed values come from native enum types and `CHECK (col IN (...))` constraints.
  Value sets enforced only in Python are not in the database, so the ERD cannot show them.
- Cardinality: a foreign key is N:1, or 1:1 when its columns are unique. A table whose
  primary key is made only of foreign keys to two or more tables is an N:M junction.
- Change the page's look or behaviour in `backend/tools/erd_template.html` and the
  extraction in `backend/tools/generate_erd.py`, then regenerate. The HTML is output only.
- The generator writes no connection details into the page.
