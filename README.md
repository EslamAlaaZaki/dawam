# DAWAM

DAWAM (Data Analysis & Warehouse Architecture Modeler) helps teams analyse source
systems and design a data warehouse from them, with every design artifact traceable
back to the real source schema. The product spec is [`docs/spec.md`](docs/spec.md);
the domain glossary is [`CONTEXT.md`](CONTEXT.md).

Licensed under the [Apache License 2.0](LICENSE).

## Run DAWAM

You need Docker with Docker Compose v2.24 or newer.

Every setting has a default except `DAWAM_ENCRYPTION_KEY`, the key that encrypts the
credentials DAWAM stores: neither Compose nor DAWAM starts without it. Like every
secret, it is read only from the environment (or `.env`). Generate one per
installation, keep it secret and back it up; stored credentials cannot be read
without it.

```sh
cp .env.example .env   # see the comments inside
# set DAWAM_ENCRYPTION_KEY in .env to the output of:
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
docker compose up --build
```

Then open <http://localhost:8000> and sign in. To get the first admin account, set
`DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` in `.env` before the first start (see
[Sign in](#sign-in)). Compose starts three services:

| Service  | What it is |
|----------|------------|
| `app`    | The FastAPI backend, which also serves the web UI. Applies database migrations on startup. |
| `worker` | The background worker process. |
| `db`     | PostgreSQL 16, the only metadata store (data in the `db-data` volume). |

An optional local LLM server is declared but off by default:
`docker compose --profile ollama up`.

Useful endpoints:

- `GET /healthz`: 200 when the process is up.
- `GET /readyz`: 200 only when the database is reachable and migrations are at head
  (503 `not_ready` otherwise). Compose uses it as the `app` health check.
- `GET /api/v1/version`, and the API docs at `/api/v1/docs`
  (OpenAPI spec at `/api/v1/openapi.json`). The docs page loads Swagger UI from
  cdn.jsdelivr.net, so the browser needs internet access to show it.

Every response carries the security headers of the spec's baseline: a
Content-Security-Policy (with `frame-ancestors 'none'`), `X-Content-Type-Options:
nosniff` and `Referrer-Policy: same-origin`. For anything beyond local use, put DAWAM
behind a TLS-terminating reverse proxy and set `DAWAM_FORWARDED_ALLOW_IPS` to the
proxy's address, so the app trusts its `X-Forwarded-Proto`; requests that arrived over
HTTPS then also get `Strict-Transport-Security` (see `.env.example`).

Logs are JSON lines on stdout (`docker compose logs -f app`). Every request gets a
request id: send `X-Request-ID` to choose it, and it is returned in the response and
on every log line for that request.

### Sign in

- **First admin.** When the app starts and no admin exists, it creates one from
  `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` (the spec's `ADMIN_EMAIL` /
  `ADMIN_PASSWORD`, with DAWAM's `DAWAM_` prefix). Once an admin exists, later starts
  never create or change one.
- **Sessions** are stored in the database. The browser holds only a random token in
  the `dawam_session` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when served over
  HTTPS). A session ends after `DAWAM_SESSION_IDLE_TIMEOUT_HOURS` (default 8) without
  a request, or `DAWAM_SESSION_ABSOLUTE_TIMEOUT_DAYS` (default 14) after sign-in.
  Behind a TLS-terminating reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's
  address so uvicorn trusts its `X-Forwarded-Proto`.
- **CSRF.** Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` must send the value
  of the `dawam_csrf` cookie in the `X-CSRF-Token` header (double submit); any
  response sets that cookie for a client without one.
- Endpoints: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/me`.

## Develop

Repository layout:

```
backend/    FastAPI app (Python 3.12+), Alembic migrations, tests, tools/
frontend/   React + TypeScript + Vite app, React Router, TanStack Query, generated API client
scripts/    generate-api-client.sh, check-api-client.sh
compose.yaml, Dockerfile, .env.example
```

### Backend

```sh
cd backend
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check . && ruff format --check .   # lint
python tools/check_boundaries.py        # module-boundary check
pytest                                  # tests (needs Docker, see below)
```

To run the API outside Docker, point it at any PostgreSQL 16 server, for example:

```sh
docker run -d --name dawam-dev-db -p 5432:5432 \
  -e POSTGRES_USER=dawam -e POSTGRES_PASSWORD=dawam postgres:16
export DAWAM_DATABASE_URL=postgresql+psycopg://dawam:dawam@localhost:5432/dawam
export DAWAM_ENCRYPTION_KEY=...   # a key you generated once (see above); keep reusing it
python -m dawam serve     # API on :8000, migrates on startup
python -m dawam worker    # background worker
```

Code is organised as a modular monolith: business modules live in
`backend/src/dawam/modules/` and may use each other only through their public
interface; shared infrastructure lives in `backend/src/dawam/platform/`. Read
[`backend/src/dawam/modules/README.md`](backend/src/dawam/modules/README.md) before
adding a module or tables. Migrations are in `backend/src/dawam/migrations/`; create
one with `alembic revision --autogenerate -m "..."` in `backend/`.

### Frontend

```sh
cd frontend
npm ci
npm run dev      # http://localhost:5173, proxies /api to http://localhost:8000
                 # (override with DAWAM_API_URL)
npm run lint
npm test         # Vitest + Testing Library
npm run build
```

### The generated API client

The frontend never hand-writes API types. `frontend/src/api/openapi.json` is exported
from the backend and `frontend/src/api/schema.d.ts` is generated from it with
openapi-typescript; requests go through openapi-fetch (`frontend/src/api/client.ts`).
After changing any API route or schema, regenerate and commit both files:

```sh
scripts/generate-api-client.sh   # needs the backend installed and `npm ci` done
```

CI fails if they are out of date (`scripts/check-api-client.sh`); the backend test
`tests/test_openapi_client_is_current.py` catches a stale spec locally too.

## Tests

Backend tests (`backend/tests/`) drive the HTTP API through a test client against a
real PostgreSQL 16 that Testcontainers starts once per test run, so **Docker must be
running**. The harness in `backend/tests/conftest.py` provides:

- `anonymous_client`: a test client with no session (entering it runs app startup,
  including migrations);
- `signed_in_client`: a client signed in through the API as `signed_in_user` (a
  regular user); it sends the CSRF token on every request;
- `create_user`: creates a user (regular by default) and returns its credentials;
  sign in with `tests.helpers.sign_in(client, email, password)`;
- `clock`: the app's clock, a `FakeClock` that tests move with `clock.advance(...)`;
- `outbox`: every email the app sends, readable as `outbox.messages` /
  `outbox.sent_to(address)`;
- `jobs`: the job runner; background work runs inline in tests, before `submit`
  returns;
- `fresh_database_url`: an empty, unmigrated database.

Application tables are emptied between tests.

CI (GitHub Actions, `.github/workflows/ci.yml`) runs on every push and pull request:
backend lint and tests, the module-boundary check, frontend lint, tests and build, and
the API client staleness check.
