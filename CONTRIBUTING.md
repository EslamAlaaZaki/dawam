# Contributing to DAWAM

Thank you for helping build DAWAM. This guide covers setting up a development
environment, the project's conventions, running the tests and getting a pull request
merged.

- [Ways to contribute](#ways-to-contribute)
- [Before you start](#before-you-start)
- [Development setup](#development-setup)
- [Project structure](#project-structure)
- [Architecture rules](#architecture-rules)
- [Making common changes](#making-common-changes)
- [Code style](#code-style)
- [Tests](#tests)
- [Continuous integration](#continuous-integration)
- [Pull request workflow](#pull-request-workflow)
- [License](#license)

## Ways to contribute

- **Report a bug.** Open a [GitHub issue](https://github.com/EslamAlaaZaki/dawam/issues)
  with the steps to reproduce it, what you expected, what happened, and the relevant log
  lines (`docker compose logs app`). Never paste your `.env` or any secrets.
- **Suggest a feature.** Open an issue describing the problem you want solved. Check
  [`docs/spec.md`](docs/spec.md) first, because many features are already specified.
- **Improve the documentation.** Small fixes can go straight to a pull request.
- **Write code.** Pick an issue labelled `ready-for-human`, or comment on one you would
  like to take.

Issue labels:

| Label | Meaning |
|-------|---------|
| `needs-triage` | Not yet evaluated by a maintainer |
| `needs-info` | Waiting for more information from the reporter |
| `ready-for-human` | Fully specified and open for a contributor |
| `ready-for-agent` | Fully specified and queued for an AI coding agent |
| `wontfix` | Will not be done |

## Before you start

1. **Learn the vocabulary.** [`CONTEXT.md`](CONTEXT.md) is the glossary (Workspace,
   Source System, Snapshot, Change Set, Layer…). Use these terms in code, UI text and
   pull requests.
2. **Read the relevant part of the spec.** [`docs/spec.md`](docs/spec.md) is large, so
   search for the heading you need rather than reading it end to end.
3. **Check the decisions.** [`docs/adr/`](docs/adr/) records choices that are settled,
   such as forward-only changes with no revert (ADR 0001) and FastAPI as the only backend
   (ADR 0003).
4. **Discuss large changes first.** For a new module, a schema redesign or a new
   dependency, open an issue before writing code.

## Development setup

### Prerequisites

| Tool | Version | Used for |
|------|---------|----------|
| Git | any recent | |
| Python | 3.12+ | Backend |
| Node.js | 22.12+ (with npm) | Frontend and end-to-end tests |
| Docker | with Compose v2.24+ | The development database and the backend tests (Testcontainers) |

On Windows, run the shell scripts in `scripts/` from Git Bash or WSL.

### 1. Clone the repository

```sh
git clone https://github.com/EslamAlaaZaki/dawam.git
cd dawam
```

### 2. Start a development database

```sh
docker run -d --name dawam-dev-db -p 5432:5432 \
  -e POSTGRES_USER=dawam -e POSTGRES_PASSWORD=dawam pgvector/pgvector:pg16
```

Use `docker start dawam-dev-db` to start it again later.

### 3. Set up the backend

```sh
cd backend
python -m venv .venv
. .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Create `backend/.env`. The app reads it from its working directory, and Git ignores it.
Generate the key once and keep reusing it, or credentials stored in your development
database become unreadable.

```sh
DAWAM_DATABASE_URL=postgresql+psycopg://dawam:dawam@localhost:5432/dawam
DAWAM_ENCRYPTION_KEY=<output of: openssl rand -base64 32>
DAWAM_ADMIN_EMAIL=admin@example.com
DAWAM_ADMIN_PASSWORD=<at least 10 characters>
DAWAM_PUBLIC_URL=http://localhost:3000
```

Run the API and the worker in two terminals:

```sh
python -m dawam serve     # http://localhost:8000; applies migrations on startup
python -m dawam worker    # background jobs
```

### 4. Set up the frontend

```sh
cd frontend
npm ci
npm run dev               # http://localhost:3000
```

Open <http://localhost:3000> and sign in with the admin from `backend/.env`. In
development, Next.js forwards `/api`, `/healthz` and `/readyz` to the API at
`DAWAM_DEV_API_URL` (default `http://localhost:8000`), so no proxy is needed.

### 5. Optional: a model for AI features

The quickest option is Ollama: `docker run -d -p 11434:11434 --name ollama ollama/ollama`,
then `docker exec ollama ollama pull qwen2.5:1.5b`. Add it in **Admin > LLM** as an
OpenAI-compatible provider at `http://localhost:11434/v1`. See
[`docs/install.md`](docs/install.md#local-llm-models) for other servers.

To run the full stack in containers instead, see
[`deploy/README.md`](deploy/README.md).

## Project structure

```
backend/
  src/dawam/
    modules/        business modules (auth, workspaces, sources, warehouse, llm, …)
    platform/       shared infrastructure: config, db, crypto, storage, email, logging
    migrations/     Alembic migrations
    app.py          FastAPI application factory
    worker.py       background worker
  tests/            pytest suite (HTTP API tests against real PostgreSQL)
  tools/            check_boundaries.py and other developer tools
frontend/
  src/
    app/            Next.js routes (App Router)
    api/            openapi.json, generated schema.d.ts, client and TanStack Query hooks
    auth/ admin/ profile/ shell/ workspaces/   screens
    security/       security headers (with proxy.ts)
    test/           test harness for component tests
e2e/                Playwright end-to-end smoke test
deploy/             nginx entry proxy, all-in-one Compose file, files for running without Docker
docs/               spec, ADRs, install, operations and reference guides
scripts/            API client generation and staleness check
compose.yaml        the default Docker Compose stack
Dockerfile          the image for the `app` and `worker` services
```

## Architecture rules

- **The backend is a modular monolith.** Each business capability is a module in
  `backend/src/dawam/modules/` that owns its tables and exposes a narrow public interface
  through its `__init__.py`. Other modules may import only that interface, never another
  module's `tables` or `internal`. Read
  [`backend/src/dawam/modules/README.md`](backend/src/dawam/modules/README.md) before
  adding a module or tables. `python tools/check_boundaries.py` enforces these rules, and
  so does CI.
- **FastAPI is the only backend.** The frontend has no API routes, no server actions and
  no database access. Screens are client components that call the API through the
  generated client (ADR 0003).
- **Nothing changes silently.** Changes that come from the AI, a regeneration, a sync or
  propagation are offered to the user as Change Sets. Never apply them directly.
- **Forward-only.** There is no revert or version history (ADR 0001). The audit trail
  records old values.
- **Secrets are encrypted at rest** with `DAWAM_ENCRYPTION_KEY` (see
  `platform/crypto.py`), are never returned by the API, and never appear in logs or
  error messages.
- **Source databases are read-only.** DAWAM never writes to a source.

## Making common changes

| When you… | Also… |
|-----------|-------|
| Change a table | Add an Alembic migration: in `backend/`, run `alembic revision --autogenerate -m "describe the change"`, then review the generated file. Migrations run when the app starts. |
| Add or change an API route or schema | Regenerate the frontend client with `scripts/generate-api-client.sh` and commit `frontend/src/api/openapi.json` and `schema.d.ts`. CI fails if they are stale. |
| Add an endpoint | Add its row to the permission matrix in `backend/tests/authz/matrix.py`. The permission suite fails for any route without one. |
| Add a setting | Add it to `platform/config.py` with a safe default, and document it in `.env.example`. |
| Add a dependency | Add it to `backend/pyproject.toml` or `frontend/package.json` and explain why in the pull request. Prefer the standard library and existing dependencies. |
| Change behaviour users or operators see | Update the matching doc: `README.md`, `deploy/README.md`, `docs/reference.md`, `docs/install.md` or `docs/operations.md`. |

## Code style

**Backend:** Ruff, with a line length of 100 and the `E F W I B UP SIM RUF` rule sets.

```sh
cd backend
ruff check . && ruff format --check .   # or: ruff check --fix . && ruff format .
python tools/check_boundaries.py
```

**Frontend:** ESLint with the Next.js rules, and TypeScript in strict mode.

```sh
cd frontend
npm run lint
npm run build    # also type-checks
```

General guidance:

- **Write tests first** where you can. Backend tests go through the HTTP API, so they
  test behaviour, not internals.
- Use the glossary terms from `CONTEXT.md` in names, messages and UI text.
- Match the surrounding code: its naming, its comment density and its idioms. Comments
  explain *why*, not *what*.
- Error responses use stable machine-readable codes (`409 version_conflict`,
  `403 registration_closed`); add new codes in the same style.
- Keep pull requests focused: one issue, one concern.

## Tests

### Backend

Backend tests (`backend/tests/`) drive the HTTP API through a test client against a real
PostgreSQL 16 that Testcontainers starts once per run, so **Docker must be running**.

```sh
cd backend
pytest                         # the default suite
pytest tests/test_foo.py -k x  # one file or test
pytest -m oracle               # Oracle connector tests (slow, large image)
pytest -m ollama               # needs a running Ollama (DAWAM_OLLAMA_URL)
pytest -m real_model           # DW generation evaluation against a real model (before a release)
```

The SQL Server connector tests also start a SQL Server 2022 container
(`mcr.microsoft.com/mssql/server`, about 1.5 GB to pull, x86-64 only) on first use, and
the MySQL/MariaDB connector tests start MySQL 8.4 and MariaDB 11.4 containers.

The harness in `backend/tests/conftest.py` provides these fixtures:

| Fixture | What it gives you |
|---------|-------------------|
| `anonymous_client` | A test client with no session. Entering it runs app startup, including migrations. |
| `signed_in_client` | A client signed in through the API as `signed_in_user` (a regular user). It sends the CSRF token on every request. |
| `admin_client` | The same, signed in as `admin_user` (an admin). |
| `create_user` | Creates a user (regular by default) and returns its credentials. Sign in with `tests.helpers.sign_in(client, email, password)`. |
| `clock` | The app's clock: a `FakeClock` that tests move forward with `clock.advance(...)`. |
| `outbox` | Every email the app sends, readable as `outbox.messages` or `outbox.sent_to(address)`. It stands in for the SMTP server, so save SMTP settings first; without them, links go to the admins' list as in production. `outbox.fail_with(reason)` makes sending fail. |
| `jobs` | The job runner. Background work runs inline, before `submit` returns. |
| `fresh_database_url` | An empty, unmigrated database. |
| `roles` | A client per role of the permission matrix (anonymous, non-member user, viewer, editor, owner, non-member admin) on one Workspace (`tests/roles.py`). |

Application tables are emptied between tests.

The **permission suite** (`tests/authz/test_permission_matrix.py`) sends every endpoint
as each of those roles and checks the outcome (allowed, 401, 403 or 404) against the
spec's §4.3 matrix. It discovers routes from the app itself, so every new endpoint needs
a row in `tests/authz/matrix.py`.

`tests/test_openapi_client_is_current.py` catches a stale API client locally.

### Frontend

```sh
cd frontend
npm test         # Vitest + Testing Library
```

Component tests render screens without Next.js. `src/test/TestApp.tsx` assembles pages and
layouts the way Next.js routes them, `src/test/navigation.ts` and `link.tsx` stand in for
`next/navigation` and `next/link`, and each test fakes the API behind the generated
client (see `src/test/fakeApi.tsx`).

### End-to-end

One Playwright journey runs against the full Compose stack, using a fake LLM provider and
a seeded source database. See [`e2e/README.md`](e2e/README.md) to run it locally. CI runs
it on every pull request.

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs these jobs on every push and pull request.
All of them must pass before a merge.

| Job | What it checks |
|-----|----------------|
| Backend lint + tests | Ruff, then pytest in 4 shards (PostgreSQL and SQL Server via Testcontainers) |
| Backend Oracle connector tests | `pytest -m oracle` |
| Module-boundary check | `tools/check_boundaries.py` |
| Frontend lint + tests + build | ESLint, Vitest, `next build` |
| Edge proxy config | The nginx configuration is valid, with and without trusted proxies |
| API client is up to date | `scripts/check-api-client.sh` |
| End-to-end smoke test | Playwright against the Compose stack |

A separate weekly workflow (`assistant-ollama.yml`) tests the assistant against a real
Ollama model.

The full backend suite can take close to an hour. If a job fails with Docker Hub
`toomanyrequests`, it hit the image pull rate limit: re-run the job.

## Pull request workflow

1. **Branch from `main`**, named after the issue:

   | Prefix | For |
   |--------|-----|
   | `feat/<issue>-<slug>` | Features, e.g. `feat/62-schema-import-helper-queries` |
   | `fix/<issue>-<slug>` | Bug fixes |
   | `docs/<slug>` | Documentation only |
   | `chore/<slug>`, `ci/<slug>` | Tooling and CI |

2. **Commit** with a short summary of what changed, ending with the issue number:

   ```
   Schema Import helper queries per engine, with download and round-trip tests (#62)
   ```

3. **Before opening the pull request**, run locally:

   - [ ] `ruff check . && ruff format --check .` and `python tools/check_boundaries.py` in `backend/`
   - [ ] `pytest` for the areas you touched
   - [ ] `npm run lint && npm test && npm run build` in `frontend/` (if you changed it)
   - [ ] `scripts/generate-api-client.sh` (if you changed the API), with the output committed
   - [ ] A migration for any table change, and a permission-matrix row for any new endpoint
   - [ ] Updated docs and `.env.example` for anything users or operators see

4. **Open the pull request** against `main`. Say what changed and why, how you tested it,
   and include `Closes #<issue>` so the issue closes on merge. Add screenshots for UI
   changes.

5. **Review and merge.** Once CI is green and a maintainer approves, the pull request is
   merged with a merge commit and the branch is deleted. To bring your branch up to date,
   merge `main` into it rather than force-pushing a rebase during review.

## License

DAWAM is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution
you agree that it is licensed under the same terms (section 5 of the license).
