# Backend modules

DAWAM's backend is a modular monolith (spec §8.1). Each business capability is a
module in this directory. A module owns its tables and exposes a narrow public
interface; other code talks to it only through that interface.

## Layout of a module

```
dawam/modules/<name>/
  __init__.py   # PUBLIC INTERFACE: re-exports the service API, schemas and `router`
  service.py    # the service API (functions or a class) other modules call
  api.py        # FastAPI `router` for the module's /api/v1 endpoints (optional)
  tables.py     # SQLAlchemy tables on dawam.platform.db.Base (optional, private)
  internal/     # anything else (optional, private)
```

Every module has `__init__.py` and `service.py`; the rest exist only when needed.
`dawam/modules/jobs/` is the smallest real example: just `__init__.py`, re-exporting
the job runner port and its inline implementation from `service.py`.

## The rules

1. **Import another module only through its package.**
   `from dawam.modules.workspaces import WorkspaceService` is fine;
   `from dawam.modules.workspaces.tables import Workspace` or
   `from dawam.modules.workspaces import tables` is not, and neither is reaching
   a submodule by attribute access (`import dawam.modules.workspaces as ws`, then
   `ws.tables.Workspace`). Inside a module, use your own submodules freely.
   A module's public names must not reuse its own submodule names: its
   `__init__.py` must not define or import anything called `api`, `service`,
   `tables`, `internal`, … other than that submodule itself. Python would make the
   two collide anyway (importing a submodule sets the package attribute of that
   name), and it keeps `workspaces.api` meaning the submodule, never an object.
2. **A module's tables are private.** Only that module reads or writes them. To
   point at another module's rows, store the id (a database foreign key by table
   name, e.g. `sa.ForeignKey("workspaces.id")`, is fine), but never map an ORM
   `relationship()` to another module's class or query its tables. The public
   interface never exports table classes: `__init__.py` must not import from
   `tables`. The module's own code (`service.py`, `internal/`) imports `tables`,
   so importing the package still registers the tables for Alembic.
3. **`dawam.platform` is the shared kernel**: config, db, errors, logging,
   migrations and email, plus the app-wide HTTP plumbing that belongs to no
   module (request context middleware, the `/healthz` and `/readyz` probes, and
   `GET /api/v1/version`). Every module may use it; it must never import
   `dawam.modules` or `dawam.app`.
4. **Composition roots** (`dawam.app`, `dawam.worker`, `dawam.__main__`) wire
   modules together and choose concrete implementations (e.g. which
   `EmailSender`); they too use only modules' public interfaces.

`tools/check_boundaries.py`, which CI runs, enforces:

- **rule 1**: any import of another module's submodule (absolute or relative),
  and any attribute chain on an imported `dawam` package that reaches one
  (names a function, lambda, class or comprehension binds locally shadow the
  import there, as in Python); and any public name in a module's `__init__.py`
  that reuses one of its submodule names;
- **rule 2, in part**: a module's `__init__.py` must not import its own `tables`;
- **rule 3**: nothing in `dawam.platform` imports or reaches `dawam.modules` or
  `dawam.app`.

```
cd backend && python tools/check_boundaries.py
```

The rest of rule 2 is left to code review. The checker reads
imports and attribute chains only, so it does not see raw SQL naming another
module's tables, `importlib` / `__import__`, lookups such as
`Base.metadata.tables["workspaces"]`, `relationship("Workspace")` string targets,
or a table class re-exported indirectly (e.g. via `service.py`).

## Adding a module

1. Create `dawam/modules/<name>/` with an `__init__.py` that defines `__all__`.
2. Add it to `ALL_MODULES` in `dawam/modules/__init__.py`. The app mounts its
   `router` (if it has one) under `/api/v1`, and Alembic sees its tables
   (imported by its service code, never by `__init__.py`; see rule 2).
3. If it has tables: `cd backend && alembic revision --autogenerate -m "add <name>"`
   (with `DAWAM_DATABASE_URL` pointing at a migrated database), then review the
   generated revision in `dawam/migrations/versions/`.
4. Raise `dawam.platform.errors.ApiError` with a stable `code` for expected
   failures; never build error responses by hand.
5. Test behaviour through the HTTP API (`anonymous_client`, later
   `signed_in_client`) or the public service interface, not internals (spec §10).

## Planned modules

`auth`, `admin`, `workspaces`, `sources`, `schema_import`, `analysis`,
`documents`, `staging`, `design`, `mapping`, `lineage`, `propagation`, `scoring`,
`collaboration`, `exports`, `jobs`, `pii`, `kpi_suggestions`, `files`,
`assistant`, `llm_gateway`. Each is created by the ticket that first needs it.
