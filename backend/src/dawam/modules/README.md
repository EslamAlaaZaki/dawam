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
`dawam/modules/activity/` is a small real example: a service, tables and nothing else.

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
   email (the SMTP transport, `EmailSender`, and the `Mailer` port; the delivery
   service itself, with its SMTP settings and undelivered links, is the `mail`
   module), crypto (`SecretBox`: AES-256-GCM under `DAWAM_ENCRYPTION_KEY`, for any
   credential a module must store and use later), the clock and running
   migrations (`dawam.platform.migrations` drives
   Alembic and points it at the scripts by path; the scripts themselves live in
   `dawam.migrations`, a composition root, not in the kernel), plus the app-wide
   HTTP plumbing that belongs to no module (request context, security headers and
   CSRF middleware, the `/healthz` and `/readyz` probes, `GET /api/v1/version`
   and the API docs page). Every module may use it; it must never import
   `dawam.modules` or `dawam.app`.
4. **Composition roots** (`dawam.app`, `dawam.worker`, `dawam.__main__` and
   `dawam.migrations`) may import `dawam.modules`; they wire modules together and
   choose concrete implementations (e.g. which `EmailSender`), using only
   modules' public interfaces. `dawam/migrations/env.py` imports `dawam.modules`
   so that every module's tables are registered on `Base.metadata` before Alembic
   compares or migrates.
5. **No import cycles: a module that another module imports gets nothing back by
   import.** `auth` is upstream of every module (any module may import it for
   `CurrentUser`, `SecurityEventRecorder`, ...), so it imports none; `notifications`
   (the `Notification` rows) imports only `auth`, so `workspaces` (the `can()` policy)
   imports `auth`, `notifications` and `activity` and nothing else. What such a module needs from a module
   that imports it, it gets as a kernel port that a composition root fills (rule 4).
   Email is the example: `mail` imports `auth` and `workspaces`, so those two send
   through the `dawam.platform.email.Mailer` port (`app.state.mailer`, the `mail`
   module's `MailService`); every other module imports `dawam.modules.mail` through
   its package (rule 1). Either way every email goes through that one service.
   `activity` (the Workspace activity feed) is the same kind of module: it imports
   only `auth`, so every module (`sources` among them) records events with
   `dawam.modules.activity.record_activity(db, ...)` in its own transaction, and the
   `workspaces` router serves the feed (`GET /workspaces/{id}/activity`), authorizing
   through its policy before calling `ActivityService.list`. `audit` (the audit trail of
   critical entities, `record_audit(db, ...)`) imports nothing from other modules and is
   used the same way.
   `jobs` imports `auth`, `notifications` and `workspaces`, so `workspaces` cannot call
   it when a Workspace is archived (archiving cancels its jobs): it calls the kernel port
   `dawam.platform.hooks.WorkspaceArchivedHook` (`app.state.on_workspace_archived`, which
   `dawam.app` sets to `JobService.cancel_for_workspace`).

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

The rest of rule 2 is left to code review, and so is everything the checker
cannot see. It reads imports and attribute chains only, so it misses:

- raw SQL naming another module's tables, lookups such as
  `Base.metadata.tables["workspaces"]`, and `relationship("Workspace")` string
  targets;
- a table class re-exported indirectly (e.g. via `service.py`);
- dynamic access: `importlib` / `__import__`, `getattr(workspaces, "tables")`,
  `sys.modules["dawam.modules.workspaces.tables"]`;
- a module object rebound by assignment (`ws = workspaces`, then `ws.tables`);
  a name a scope both imports and rebinds counts as shadowed in that scope;
- string annotations (`"workspaces.tables.Workspace"`).

## Adding a module

1. Create `dawam/modules/<name>/` with an `__init__.py` that defines `__all__`.
2. Add it to `ALL_MODULES` in `dawam/modules/__init__.py`. The app mounts its
   `router` (if it has one) under `/api/v1`, and Alembic sees its tables
   (imported by its service code, never by `__init__.py`; see rule 2).
   `tests/test_module_tables_registered.py` fails if importing `dawam.modules`
   leaves any module's `tables.py` unimported.
3. If it has tables: `cd backend && alembic revision --autogenerate -m "add <name>"`
   (with `DAWAM_DATABASE_URL` pointing at a migrated database), then review the
   generated revision in `dawam/migrations/versions/`.
4. Raise `dawam.platform.errors.ApiError` with a stable `code` for expected
   failures; never build error responses by hand.
5. Routes that need a signed-in user take a parameter annotated
   `dawam.modules.auth.CurrentUser`; anonymous requests then get `401
   unauthenticated`. Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` already
   requires the double-submit CSRF token (`dawam.platform.csrf`); add nothing.
6. Test behaviour through the HTTP API (`anonymous_client`, `signed_in_client`)
   or the public service interface, not internals (spec §10). The one exception
   is a deliberate storage-property check (e.g. a token is stored only hashed),
   which may read the module's own tables; say so in the test's docstring.
7. **Authorize through the policy, never by comparing roles.** Every decision goes
   through `dawam.modules.workspaces.can(user, action, resource)` (spec §6.2); add
   an `Action` and its rule there for a new row of the §4.3 matrix. For a
   Workspace-scoped resource, load it, take the `workspace_id` stored on it (never
   one from the URL or body alone) and call `WorkspaceService.authorize(user,
   action, workspace_id)`: non-members (admins included) get 404, members whose role
   is too low get 403. Code that changes Workspace members or roles must lock the
   Workspace row (`SELECT ... FOR UPDATE`) first: the deferred "at least one owner"
   trigger cannot stop two concurrent transactions each removing a different owner.
8. **Every route needs a row in the permission suite**
   (`tests/authz/test_permission_matrix.py`): the endpoint and what each role gets,
   copied from the §4.3 matrix. The suite fails for a route without one.

## Planned modules

`auth`, `admin`, `workspaces`, `notifications`, `sources`, `schema_import`, `analysis`,
`documents`, `staging`, `design`, `mapping`, `lineage`, `propagation`, `scoring`,
`collaboration`, `exports`, `jobs`, `pii`, `kpi_suggestions`, `files`,
`assistant`, `llm_gateway`, `mail`. Each is created by the ticket that first needs it.
