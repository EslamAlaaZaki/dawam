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

Then open <http://localhost:8000> (`DAWAM_PORT`) and sign in. To get the first admin
account, set `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` in `.env` before the first
start (see [Sign in](#sign-in)). Compose starts five services:

| Service  | What it is |
|----------|------------|
| `edge`   | nginx (`deploy/nginx/`), the only service published on the host: it sends `/api/`, `/healthz` and `/readyz` to `app` and everything else to `web`, so the browser sees a single origin. |
| `web`    | The Next.js web UI (pages only). |
| `app`    | The FastAPI backend: the API, and the only place that authenticates, authorizes and stores data. Applies database migrations on startup. |
| `worker` | The background worker process. |
| `db`     | PostgreSQL 16, the only metadata store (data in the `db-data` volume). |

Everything else is reachable only inside the Compose network, so every request
passes through `edge`. It overwrites `X-Forwarded-For` and `X-Forwarded-Proto` with
the client's address and scheme, and `app` believes those headers from `edge`'s
address alone (`DAWAM_FORWARDED_ALLOW_IPS`, set by Compose): whatever a client sends
in them is ignored. The Compose network has a fixed subnet (`172.30.126.0/24`) so
that `edge` can have a fixed address; if it clashes with a network on your host,
change the three addresses in `compose.yaml` together.

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
nosniff` and `Referrer-Policy: same-origin`. FastAPI sets them on the API responses,
and `web` on the pages and files it serves. Next.js puts inline scripts in every page,
so the pages' policy allows scripts only from DAWAM itself or carrying a nonce that is
new on every request (`frontend/src/security/headers.ts`); that is why every page is
rendered per request.

For anything beyond local use, put DAWAM behind a TLS-terminating reverse proxy in
front of `edge` (`DAWAM_PORT`) and list the proxy's addresses in
`DAWAM_TRUSTED_PROXY_CIDRS`. From those addresses only, `edge` takes the client from
`X-Forwarded-For` and the scheme from `X-Forwarded-Proto`; requests that arrived over
HTTPS then get `Strict-Transport-Security` (pages and API) and `Secure` cookies (see
`.env.example`).

Logs are JSON lines on stdout (`docker compose logs -f app web edge`). Every request gets a
request id: send `X-Request-ID` to choose it, and it is returned in the response and
on every log line for that request.

### Sign in

- **First admin.** When the app starts and no admin exists, it creates one from
  `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` (the spec's `ADMIN_EMAIL` /
  `ADMIN_PASSWORD`, with DAWAM's `DAWAM_` prefix). Once an admin exists, later starts
  never create or change one. If no admin exists but a non-admin user already has
  `DAWAM_ADMIN_EMAIL`, startup fails with an error naming that variable, and the user
  is left unchanged.
- **Sessions** are stored in the database. The browser holds only a random token in
  the `dawam_session` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when served over
  HTTPS). A session ends after `DAWAM_SESSION_IDLE_TIMEOUT_HOURS` (default 8) without
  a request, or `DAWAM_SESSION_ABSOLUTE_TIMEOUT_DAYS` (default 14) after sign-in.
  Behind a TLS-terminating proxy, cookies are `Secure` only once
  `DAWAM_FORWARDED_ALLOW_IPS` trusts it (see above).
- **CSRF.** Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` must send the value
  of the `dawam_csrf` cookie in the `X-CSRF-Token` header (double submit); any
  response sets that cookie for a client without one.
- **Lockout.** After `DAWAM_LOGIN_MAX_FAILURES` (default 5) consecutive failed
  sign-ins an account is locked for `DAWAM_LOGIN_LOCKOUT_MINUTES` (default 15): even
  the right password gets `429 account_locked` (with `Retry-After`) until then. A
  successful sign-in resets the count, and failures more than a day apart do not add
  up. An email without an account behaves the same, so the lock does not reveal which
  emails have accounts.
- **Rate limiting per address.** A client address with `DAWAM_LOGIN_IP_MAX_FAILURES`
  (default 20) failed sign-ins in the last `DAWAM_LOGIN_IP_WINDOW_MINUTES` (default
  15) gets `429 too_many_attempts` until the oldest leaves the window. Only that
  address waits; users behind one shared NAT address share its limit. Both limits are
  kept in PostgreSQL, so they need no extra service and hold across restarts.
- **Security events.** Successful and failed sign-ins, lockouts, sign-ups, password
  changes, signing out everywhere and changes to the registration settings are
  recorded (who, if known, the account, the client address and the time; never a
  password) in the `security_events` table, for the admin's security log.
- **Passwords** must be at least 10 characters long and not on DAWAM's list of
  common passwords (compared ignoring case), whenever one is set: sign-up, the first
  admin, and every password change. The list ships with DAWAM, so it works offline: it
  is zxcvbn's list of 47,000 common passwords, under the MIT licence
  (`backend/src/dawam/modules/auth/internal/common_passwords.LICENSE.txt`).
- **Self-registration** is off until an admin turns it on (Admin settings, or
  `PUT /api/v1/admin/settings`), optionally only for some email domains (exact
  matches; list subdomains separately). While it is off the login page shows no
  sign-up link and `POST /api/v1/auth/register` answers `403 registration_closed`; an
  email outside the allowed domains gets `403 email_domain_not_allowed`. An address
  that tried to sign up `DAWAM_REGISTER_IP_MAX_ATTEMPTS` (default 10) times in the
  last `DAWAM_REGISTER_IP_WINDOW_MINUTES` (default 60) gets `429 too_many_attempts`.
- **Profile.** Users edit their display name, change their password (which needs the
  current one and signs out every other session of theirs) and sign out everywhere. A
  wrong current password counts towards the lockout like a failed sign-in.
- Endpoints: `POST /api/v1/auth/login`, `/auth/logout`, `/auth/logout-all`,
  `/auth/register`, `/auth/password/change`, `/auth/password/forgot`,
  `/auth/password/reset`; `GET /api/v1/auth/registration` (is
  sign-up open?); `GET|PATCH /api/v1/me`; `GET|PUT /api/v1/admin/settings` (admins
  only).
- **Forgotten passwords.** The sign-in page links to "Forgot your password?", which
  emails a reset link (`POST /api/v1/auth/password/forgot`; the answer is the same
  whether or not the email belongs to a user). The link is single-use and valid
  for 30 minutes; DAWAM stores only its SHA-256. Setting a new password with it
  (`POST /api/v1/auth/password/reset`) ends every session of that user. Links start
  with `DAWAM_PUBLIC_URL` (default `http://localhost:8000`), never with the
  request's host.

### Email

An admin sets up SMTP in the web UI (**Email settings**, `GET|PUT|DELETE
/api/v1/admin/smtp`): host, port, `none` / `starttls` / `tls` (certificates are
always verified), optional username and password, and the From address. **Send test
email** (`POST /api/v1/admin/smtp/test`) sends one right away and shows the server's
error if it fails. The SMTP password is stored encrypted with `DAWAM_ENCRYPTION_KEY`
(AES-256-GCM) and never returned (the API says `has_password` instead); changing the
host, port or username needs it entered again, so it cannot be sent to another
server unseen. The SMTP host is whatever the admin enters, so DAWAM will connect to
any address it can reach, internal ones included (the test email shows the server's
error). That is accepted because only admins can set it; keep admin accounts few.

Forgot-password is throttled without telling anyone: at most one reset email per
account per `DAWAM_PASSWORD_RESET_COOLDOWN_MINUTES` (5), and at most
`DAWAM_PASSWORD_RESET_IP_MAX_REQUESTS` (10) per client address per
`DAWAM_PASSWORD_RESET_IP_WINDOW_MINUTES` (60). A throttled request gets the same
answer, sends nothing and stores nothing.

**Without SMTP** (air-gapped installs), or when sending fails, links DAWAM would
email are kept for admins instead: **Links to share** (`GET
/api/v1/admin/undelivered-links`) lists each with its recipient, so an admin can
copy it to them. A link leaves the list when it expires, when it stops working
(a used or voided reset link) or when an admin removes it;
it is stored encrypted too.

### Workspaces

Any signed-in user can create a Workspace and becomes its owner. The list shows only
the Workspaces you are a member of, with your role (owner, editor or viewer); only
owners edit a Workspace's details. Anyone who is not a member, admins included, gets
404 for a Workspace, so its existence is not revealed. Edits carry the `version` the
client last saw, and a stale one gets `409 version_conflict`. List endpoints are
paged with `limit` and an opaque `cursor` (the previous page's `next_cursor`).

- Endpoints: `GET|POST /api/v1/workspaces`, `GET|PATCH /api/v1/workspaces/{workspace_id}`.

## Develop

Repository layout:

```
backend/    FastAPI app (Python 3.12+), Alembic migrations, tests, tools/
frontend/   Next.js (App Router) + TypeScript app, TanStack Query, generated API client,
            its own Dockerfile (the `web` service)
deploy/     nginx/: the `edge` service (Dockerfile, config template, trusted-proxy script)
scripts/    generate-api-client.sh, check-api-client.sh
compose.yaml, Dockerfile (the `app` and `worker` image), .env.example
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
npm run dev      # http://localhost:3000; only `next dev` forwards /api, /healthz and
                 # /readyz itself, to http://localhost:8000 (DAWAM_DEV_API_URL)
npm run lint     # ESLint, with the Next.js rules
npm test         # component tests: Vitest + Testing Library
npm run build    # next build (also type-checks)
```

The build is a standalone server (`output: "standalone"`), which `next start` cannot
run. The `web` image runs it; to run it yourself after `npm run build`:

```sh
cp -r .next/static .next/standalone/.next/
node .next/standalone/server.js   # pages only: put edge (or another proxy) in front for /api
```

The app lives in `frontend/src/`: routes in `app/` (`login/`, `signup/`,
`forgot-password/`, `reset-password/`, and the signed-in pages in the `(signed-in)`
group, `profile/` and `admin/` (settings, email) among them, whose layout sends anyone
not signed in to `/login?from=<page>`), screens in `auth/`, `profile/`, `admin/`,
`shell/` and `workspaces/`, the API client and its
TanStack Query hooks in `api/`, and the security headers in `security/` and
`proxy.ts`. Screens are client components that call the API from the browser;
server components only lay out the static shell. The frontend has no API routes,
server actions or database access: FastAPI is the only backend (ADR 0003).

Component tests render the screens without Next.js: `src/test/TestApp.tsx` puts the
pages and layouts together as Next.js routes them, `src/test/navigation.ts` stands in
for `next/navigation` (and `link.tsx` for `next/link`), and each test fakes the API
behind the generated client (`src/test/fakeApi.tsx` fakes the account API).

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
- `admin_client`: the same, signed in as `admin_user` (an admin);
- `create_user`: creates a user (regular by default) and returns its credentials;
  sign in with `tests.helpers.sign_in(client, email, password)`;
- `clock`: the app's clock, a `FakeClock` that tests move with `clock.advance(...)`;
- `outbox`: every email the app sends, readable as `outbox.messages` /
  `outbox.sent_to(address)` (it stands in for the SMTP server, so a test saves
  SMTP settings first; without them links go to the admins' list, as in
  production; `outbox.fail_with(reason)` makes sending fail);
- `jobs`: the job runner; background work runs inline in tests, before `submit`
  returns;
- `fresh_database_url`: an empty, unmigrated database;
- `roles`: a client per role of the permission matrix (anonymous, non-member user,
  viewer, editor, owner, non-member admin) on one Workspace (`tests/roles.py`).

Application tables are emptied between tests.

The permission suite (`tests/authz/test_permission_matrix.py`) sends every endpoint
as every one of those roles and checks the outcome (allowed, 401, 403 or 404)
against the spec's §4.3 matrix. It finds the routes from the app itself and fails
for any route without a row, so a new endpoint needs one (see
`tests/authz/matrix.py`).

CI (GitHub Actions, `.github/workflows/ci.yml`) runs on every push and pull request:
backend lint and tests, the module-boundary check, frontend lint, tests and build, and
the API client staleness check.
