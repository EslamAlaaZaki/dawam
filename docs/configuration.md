# Configuration reference

Every setting DAWAM reads from `.env`, grouped by area. Copy `.env.example` to `.env`
at the repository root and change what you need: only `DAWAM_ENCRYPTION_KEY` is
required. `.env.example` has a longer comment on each setting.

## How `.env` reaches the containers

- `app` and `worker` load the whole file through `env_file:` in each Compose file.
- `web` and `edge` never load it. Compose passes them only what they need:
  `DAWAM_HSTS_MAX_AGE_SECONDS` (`web`) and `DAWAM_TRUSTED_PROXY_CIDRS` (`edge`).
- Compose also fills `${...}` placeholders in the YAML from `.env`, such as
  `POSTGRES_*` and `DAWAM_PORT`. `deploy/docker-compose.yaml` lives in `deploy/`, so
  run it with `--env-file .env`, or those placeholders fall back to their defaults.
- A value set under a service's `environment:` wins over the same variable in
  `.env`. `compose.e2e.yaml` uses this to set its own admin and `DAWAM_FAKE_LLM`.

## Database (`db`)

| Variable | Default | What it does |
|---|---|---|
| `POSTGRES_USER` | `dawam` | Postgres user |
| `POSTGRES_PASSWORD` | `dawam` | Postgres password; change it for anything others can reach |
| `POSTGRES_DB` | `dawam` | Database name |

## Core app (`app`, `worker`)

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_ENCRYPTION_KEY` | **required**, no default | Encrypts stored credentials (AES-256-GCM); stored credentials can't be read without it |
| `DAWAM_DATABASE_URL` | built from `POSTGRES_*` | Only needed for an external Postgres 16 |
| `DAWAM_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING or ERROR (JSON logs) |
| `DAWAM_RUN_MIGRATIONS_ON_STARTUP` | `true` | Apply database migrations when the app starts |
| `DAWAM_WORKER_POLL_SECONDS` | `5` | How often the worker checks for jobs |
| `DAWAM_WORKER_HEARTBEAT_SECONDS` | `10` | How often a worker tells the queue it is still running its job; must be below `DAWAM_JOB_STALE_SECONDS` |
| `DAWAM_JOB_STALE_SECONDS` | `60` | A running job whose worker has been silent this long is failed as lost |
| `DAWAM_REQUEST_THREADS` | `200` | Threads for the API's blocking work; each streaming assistant answer holds one |

## File storage

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `DAWAM_STORAGE_PATH` | `/var/lib/dawam/files` in Compose (`data/files` outside it) | Local storage folder (the Compose volume) |
| `DAWAM_S3_BUCKET` | none | S3 bucket |
| `DAWAM_S3_ENDPOINT_URL` | none | S3-compatible endpoint |
| `DAWAM_S3_REGION` | none | S3 region |
| `DAWAM_S3_ACCESS_KEY_ID` | none | S3 access key |
| `DAWAM_S3_SECRET_ACCESS_KEY` | none | S3 secret |
| `DAWAM_UPLOAD_MAX_MB` | `25` | Largest upload in MB (edge caps request bodies at 100 MB) |

## AI assistant

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_ASSISTANT_MAX_TOOL_CALLS` | `25` | Tool calls allowed per chat message |
| `DAWAM_ASSISTANT_JOB_MAX_TOOL_CALLS` | `150` | Tool calls allowed per background job |
| `DAWAM_ASSISTANT_JOB_SUPERVISOR` | `false` | Run background jobs as a supervisor routing to specialist agents (ADR 0009) |
| `DAWAM_ASSISTANT_SOURCE_QUERY_SECONDS` | `120` | Seconds of source queries allowed per message |
| `DAWAM_SCORE_DEBOUNCE_SECONDS` | `2` | Wait after a design change before the Data Warehouse score is recalculated |

## Documents (document AI)

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_DOCUMENT_PARSER` | `docling` | How PDF, DOCX and XLSX uploads are read: `docling` (keeps headings, reading order and tables) or `basic` (plain text with pypdf, python-docx, openpyxl) |
| `DAWAM_DOCUMENT_OCR` | `rapidocr` | OCR for scanned PDF pages: `rapidocr` (English and Arabic) or `off` |
| `DAWAM_DOCUMENT_ACCELERATOR` | `auto` | Device for Docling and RapidOCR: `auto`, `cpu` or `cuda` (see [Document AI: CPU vs GPU](../deploy/README.md#document-ai-cpu-vs-gpu)) |

## Privacy and tracing

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_PII_NER_ENABLED` | `false` | Also mask person names and addresses (Arabic and English) with NER; needs the backend's `ner` extra and both models, or the app and worker refuse to start |
| `DAWAM_PII_NER_ENGLISH_MODEL` | `en_core_web_sm` | spaCy model package or folder for English |
| `DAWAM_PII_NER_ARABIC_MODEL` | `CAMeL-Lab/bert-base-arabic-camelbert-msa-ner` | CAMeL Tools NER model for Arabic (Hugging Face id from the local cache, or a folder) |
| `DAWAM_OTEL_ENDPOINT` | not set | OTLP/HTTP traces endpoint (e.g. a self-hosted Langfuse); tracing is off while unset |
| `DAWAM_OTEL_CAPTURE_CONTENT` | `false` | Add redacted prompts and tool content to spans (metadata only otherwise) |
| `DAWAM_OTEL_SERVICE_NAME` | `dawam` | `service.name` on the spans |

## First admin

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_ADMIN_EMAIL` | not set | Email for the admin created on first start |
| `DAWAM_ADMIN_PASSWORD` | not set | Password for that admin (at least 10 characters, not a common password) |

Set both or neither. They are used only when the app starts and no admin exists
yet; after that they are ignored, so changing them does not change an existing
admin's login.

## Sessions and sign-in protection

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_SESSION_IDLE_TIMEOUT_HOURS` | `8` | Session ends after this long without a request |
| `DAWAM_SESSION_ABSOLUTE_TIMEOUT_DAYS` | `14` | Session ends this long after sign-in, however active |
| `DAWAM_LOGIN_MAX_FAILURES` | `5` | Failed sign-ins before the account is locked |
| `DAWAM_LOGIN_LOCKOUT_MINUTES` | `15` | How long the lock lasts |
| `DAWAM_LOGIN_IP_MAX_FAILURES` | `20` | Failed sign-ins allowed from one IP address per window |
| `DAWAM_LOGIN_IP_WINDOW_MINUTES` | `15` | Length of that window |
| `DAWAM_REGISTER_IP_MAX_ATTEMPTS` | `10` | Sign-up attempts allowed from one IP address per window |
| `DAWAM_REGISTER_IP_WINDOW_MINUTES` | `60` | Length of that window |
| `DAWAM_PASSWORD_RESET_COOLDOWN_MINUTES` | `5` | Minimum time between reset emails for one account |
| `DAWAM_PASSWORD_RESET_IP_MAX_REQUESTS` | `10` | Reset emails one IP address can trigger per window |
| `DAWAM_PASSWORD_RESET_IP_WINDOW_MINUTES` | `60` | Length of that window |

## URLs, ports and HTTPS

| Variable | Default | Used by | What it does |
|---|---|---|---|
| `DAWAM_PUBLIC_URL` | `http://localhost:8000` | app | Start of every link DAWAM puts in emails |
| `DAWAM_PORT` | `8000` | Compose (edge) | Host port DAWAM is published on |
| `DAWAM_TRUSTED_PROXY_CIDRS` | not set | edge | Addresses of a TLS proxy in front of DAWAM that edge should trust |
| `DAWAM_HSTS_MAX_AGE_SECONDS` | `31536000` | app, web | HSTS max-age, sent only on HTTPS requests; `0` turns it off |

## All-in-one Compose file only

Read by `deploy/docker-compose.yaml`, not by DAWAM itself.

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_WORKERS` | `2` | Number of `worker` replicas |
| `OLLAMA_MODELS` | `qwen2.5:7b nomic-embed-text` | Models `ollama-pull` downloads at start |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | Context length Ollama runs models with; enter the same value as the model's context window in Admin > Language models |
| `MAILPIT_PORT` | `8025` | Host port (on `127.0.0.1`) of Mailpit's web UI |

## Tests only

| Variable | Default | What it does |
|---|---|---|
| `DAWAM_FAKE_LLM` | `false` | Replaces every LLM with a rule-based fake; only `compose.e2e.yaml` turns it on |

## Set outside `.env`

| Variable | Set in | Value | What it does |
|---|---|---|---|
| `DAWAM_FORWARDED_ALLOW_IPS` | Compose files | `172.30.126.10` (edge's fixed address) | The only address the app trusts for `X-Forwarded-*` headers |
| `DAWAM_EDGE_APP` | `deploy/nginx/Dockerfile` | `app:8000` | Where edge sends `/api/`, `/healthz` and `/readyz` |
| `DAWAM_EDGE_WEB` | `deploy/nginx/Dockerfile` | `web:3000` | Where edge sends everything else |
| `DAWAM_DEV_API_URL` | your shell | `http://localhost:8000` | Backend that `next dev` forwards the API to; local development only |
