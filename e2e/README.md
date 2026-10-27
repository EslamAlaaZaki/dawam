# End-to-end tests

Two Playwright projects (`playwright.config.ts`) run in a browser. CI runs both as the `e2e`
job.

- **`smoke`** (`tests/smoke.spec.ts`) proves the main path: sign in, create a Workspace, add
  a member, extract a Source Schema, generate staging, generate core (with the assistant),
  map a column and view its lineage.
- **`walkthrough`** (`tests/walkthrough.spec.ts`) repeats the core flows with the keyboard
  only. It depends on `smoke` and uses the Workspace and pending Change Set that the smoke
  journey leaves behind.

It runs against the full Compose stack with the fake LLM provider (`DAWAM_FAKE_LLM=true`:
`backend/src/dawam/modules/llm/internal/e2e.py`, answering by rule and never calling a model)
and a seeded PostgreSQL source (`e2e/source-db/init.sql`):

```sh
export DAWAM_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
export COMPOSE_FILE=compose.yaml:compose.e2e.yaml
docker compose up --build --wait
cd e2e && npm ci && npx playwright install chromium && npx playwright test
docker compose down --volumes
```

With Docker on Windows (outside WSL), Compose separates `COMPOSE_FILE` entries with `;`:
use `compose.yaml;compose.e2e.yaml`, or set `COMPOSE_PATH_SEPARATOR=:`.

`E2E_BASE_URL` points it elsewhere (default `http://localhost:8000`). The first admin comes
from the compose file and the fake model from the test itself, so use a fresh stack (volumes
removed) for each run.
