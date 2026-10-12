# End-to-end smoke test

One Playwright journey proves the main path in a browser: sign in, create a Workspace, add a
member, extract a Source Schema, generate staging, generate core (with the assistant), map a
column and view its lineage. CI runs it as the `e2e` job.

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

`E2E_BASE_URL` points it elsewhere (default `http://localhost:8000`). The first admin comes
from the compose file and the fake model from the test itself, so use a fresh stack (volumes
removed) for each run.
