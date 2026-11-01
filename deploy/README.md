# Deploying DAWAM

How to run DAWAM, from a laptop to a server. Pick a method and follow only its
section. Every setting is described in [`.env.example`](../.env.example). LLM servers
and air-gapped installs are covered in [`docs/install.md`](../docs/install.md), and
backups, upgrades and key rotation in [`docs/operations.md`](../docs/operations.md).

| Method | Use it for | Needs |
|--------|-----------|-------|
| [1. Docker Compose](#1-docker-compose) | The usual way: evaluation, or a single server | Docker + Compose v2.24+ |
| [2. All-in-one Compose file](#2-all-in-one-compose-file) | Everything in one go: DAWAM, a local LLM, a mail catcher and a demo source database | Docker + Compose v2.24+, ~10 GB disk and 8 GB RAM for the default models |
| [3. Local development](#3-local-development) | Working on the code (hot reload) | Python 3.12, Node.js 22.12+, Docker (for PostgreSQL) |
| [4. Local run without Docker](#4-local-run-without-docker) | Running the built app on your machine | Python 3.12, Node.js 22.12+, PostgreSQL 16 + pgvector, nginx |
| [5. Linux server without Docker](#5-linux-server-without-docker-systemd) | Hosts where containers are not allowed | Same as 4, plus systemd |

For the Docker Compose commands of each setup (CPU or GPU, with or without Ollama),
see [Docker Compose: the four setups](#docker-compose-the-four-setups).

## Components

Every method runs the same components. The ones inside the box are DAWAM's own; the
ones outside are systems DAWAM connects to, which you provide.

```mermaid
flowchart LR
    user(["User's browser"])

    subgraph dawam["DAWAM installation"]
        direction LR
        edge["<b>edge</b><br/>nginx entry proxy<br/>:8080 (host: DAWAM_PORT)"]
        web["<b>web</b><br/>Next.js UI<br/>:3000"]
        app["<b>app</b><br/>FastAPI API<br/>:8000"]
        worker["<b>worker</b><br/>background jobs"]
        db[("<b>db</b><br/>PostgreSQL 16<br/>+ pgvector")]
        files[("<b>file storage</b><br/>local volume<br/>or S3 bucket")]
        ollama["<b>ollama</b> (optional)<br/>local LLM server<br/>:11434"]
        searxng["<b>searxng</b><br/>web search<br/>:8080"]
    end

    subgraph external["External systems (configured in the admin console)"]
        direction TB
        llm["<b>LLM providers</b><br/>OpenAI-compatible (Ollama, vLLM, SGLang, OpenAI…)<br/>Anthropic · Azure OpenAI · Gemini · AWS Bedrock"]
        sources[("<b>Source databases</b><br/>PostgreSQL · SQL Server<br/>MySQL/MariaDB · Oracle")]
        smtp["<b>SMTP server</b><br/>outgoing email"]
    end

    user -- "HTTP" --> edge
    edge -- "/api/, /healthz, /readyz" --> app
    edge -- "everything else" --> web

    app --> db
    worker --> db
    app --> files
    worker --> files

    app -- "chat, embeddings" --> llm
    worker -- "chat, embeddings" --> llm
    app -. "optional" .-> ollama
    worker -. "optional" .-> ollama
    app -. "web search, if turned on" .-> searxng

    app -- "read schema, profile, sample<br/>(read-only)" --> sources
    worker -- "read schema, profile, sample<br/>(read-only)" --> sources

    app -- "invitations, password resets,<br/>notifications" --> smtp
```

| Component | What it is | Runs as | Required |
|-----------|-----------|---------|----------|
| **edge** | nginx entry proxy. Sends `/api/`, `/healthz` and `/readyz` to `app` and everything else to `web`, so the browser sees one address. It overwrites `X-Forwarded-For`, and the app believes that header from this address only (`DAWAM_FORWARDED_ALLOW_IPS`). The only port published on the host. | `deploy/nginx/` image; `host/nginx.conf` without Docker | Yes |
| **web** | The Next.js frontend, as a standalone Node.js server. Pages only: no API routes, no database access, no secrets. | `frontend/Dockerfile` | Yes |
| **app** | The FastAPI backend (`python -m dawam serve`). The only place that authenticates, authorizes and stores data. Applies database migrations when it starts. | Root `Dockerfile` | Yes |
| **worker** | Background jobs (`python -m dawam worker`): long assistant tasks, schema extraction from Connections, data profiling, PII scans, relationship inference, document re-indexing. Same image and settings as `app`; you can run several. | Root `Dockerfile` | Yes |
| **db** | PostgreSQL 16 with the `vector` extension: DAWAM's only metadata store, including the document search index. You can use an external server instead (`DAWAM_DATABASE_URL`). | `pgvector/pgvector:pg16` | Yes |
| **file storage** | Uploaded files and documents. A directory (`file-data` volume) or an S3-compatible bucket (`DAWAM_STORAGE_BACKEND=s3`, image built with the `s3` extra). | Volume or bucket | Yes |
| **ollama** | Optional local LLM server, started with `--profile ollama`. | `ollama/ollama` | No |
| **searxng** | Web search for the assistant's `web_search`, used once an admin adds it in Admin > Web access (base URL `http://searxng:8080`). Reached on the Compose network only; config in `searxng/settings.yml`, secret key `SEARXNG_SECRET`. | `searxng/searxng` | No |
| **LLM providers** | The models behind AI features. Types: OpenAI-compatible (Ollama, vLLM, SGLang, OpenAI and others), Anthropic, Azure OpenAI, Gemini, AWS Bedrock. Added by an admin. | External | For AI features |
| **Source databases** | The systems you analyse, reached through Connections with credentials encrypted by `DAWAM_ENCRYPTION_KEY`. Live connections: PostgreSQL, SQL Server, MySQL/MariaDB, Oracle. Schema Import from files needs no connection, with helper queries for those engines plus IBM Db2 and Teradata. | External | For live connections |
| **SMTP server** | Sends invitations, password resets and notifications. Set in Admin > Email. Until then, links are shown to admins to share. | External | No |

Network access the installation needs:

- **Inbound:** only `edge` (`DAWAM_PORT`, default 8000).
- **Outbound from `app` and `worker`:** the LLM providers and source databases you
  configure, the SMTP server (`app` only), plus the S3 endpoint if you use one, and the
  web search provider and fetched pages if an admin turns on the assistant's web access
  (off by default, [ADR 0008](../docs/adr/0008-assistant-outbound-web-access.md)). With local models and no
  external services, DAWAM runs fully offline ([`docs/install.md`](../docs/install.md),
  "Air-Gapped Installation").
- **Browser:** the API docs page (`/api/v1/docs`) loads Swagger UI from
  cdn.jsdelivr.net. Nothing else in the UI loads from outside.

Files in this folder:

| Path | Used by |
|------|---------|
| `nginx/` | The `edge` image (methods 1–2) |
| `searxng/settings.yml` | Methods 1–2: the `searxng` service's settings (JSON output on, no limiter) |
| `docker-compose.yaml` | Method 2: every component in one Compose file, HTTPS disabled |
| `docker-compose.gpu.yaml` | Methods 1–2: added after either Compose file to run document AI on an NVIDIA GPU |
| `host/nginx.conf` | Methods 4–5: the entry proxy without Docker |
| `host/dawam.env.example` | Methods 4–5: app and worker settings without Docker |
| `host/systemd/*.service` | Method 5: the `app`, `worker` and `web` services |

## Before any method: the encryption key

`DAWAM_ENCRYPTION_KEY` is the only setting with no default, and nothing starts
without it. It encrypts stored credentials (connection passwords, LLM API keys).
Generate it **once per installation**, keep it secret, and **back it up separately
from the database**. Without it, stored credentials cannot be read.

```sh
openssl rand -base64 32
# or
python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

To create the first admin account, set `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD`
before the first start (password: 10+ characters, not a common one). They are only
used while no admin exists, so you can remove them afterwards.

## Docker Compose: the four setups

You don't need to build the images first: `up -d --build` builds them and starts them
in one step. There are two Compose files, and each can run on the CPU or on an
NVIDIA GPU:

| Setup | Compose file | LLM | Document AI runs on |
|-------|--------------|-----|---------------------|
| 1. CPU, without Ollama | `compose.yaml` | An external provider you add in Admin > Language models | CPU |
| 2. GPU, without Ollama | `compose.yaml` + `deploy/docker-compose.gpu.yaml` | An external provider you add in Admin > Language models | GPU |
| 3. CPU, all-in-one | `deploy/docker-compose.yaml` | Ollama, models pulled on first start | CPU |
| 4. GPU, all-in-one | `deploy/docker-compose.yaml` + `deploy/docker-compose.gpu.yaml` | Ollama, models pulled on first start (on the GPU if you enable it) | GPU |

**For every setup, first** create `.env` in the repository root (`cp .env.example .env`,
or `copy .env.example .env` in Windows PowerShell or Command Prompt) and set
`DAWAM_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `DAWAM_ADMIN_EMAIL` and
`DAWAM_ADMIN_PASSWORD` ([above](#before-any-method-the-encryption-key)). Run every
command from the repository root.

**1. CPU, without Ollama**

```sh
docker compose up -d --build
```

**2. GPU, without Ollama**

```sh
docker compose -f compose.yaml -f deploy/docker-compose.gpu.yaml up -d --build
```

**3. CPU, all-in-one**

```sh
docker compose -f deploy/docker-compose.yaml --env-file .env up -d --build
docker compose -f deploy/docker-compose.yaml --env-file .env logs -f ollama-pull   # model download
```

**4. GPU, all-in-one**

To run Ollama on the GPU as well, first uncomment the `deploy:` block under `ollama` in
[`docker-compose.yaml`](docker-compose.yaml). Without it, only document AI uses the GPU.

```sh
docker compose -f deploy/docker-compose.yaml -f deploy/docker-compose.gpu.yaml --env-file .env up -d --build
```

After starting:

- Wait until `app`, `web` and `edge` are `healthy` in `docker compose ... ps`, then open
  `http://localhost:8000`.
- **Setups 3 and 4:** add the Ollama provider and its models in Admin > Language models,
  and give the models their roles in Admin > Roles, budgets & usage
  ([method 2](#2-all-in-one-compose-file), step 3).
- **Setups 2 and 4:** check that the worker sees the GPU
  ([Document AI: CPU vs GPU](#document-ai-cpu-vs-gpu)).

Rules for all four:

- Pass the same `-f` and `--env-file` flags to every later command (`ps`, `logs`,
  `exec`, `down`).
- Run one setup at a time. They share the same volumes, so you can switch without
  losing data: stop the current one with `docker compose ... down` (with its flags),
  then start the other. Never use `down -v`: it deletes the database and the uploads.
- Switching between CPU and GPU rebuilds the backend image, which `--build` does. The
  first GPU build is slow: the image is about 12 GB.
- Setup 1 can still start Ollama with `docker compose --profile ollama up -d`, but then
  you pull the models yourself. Setup 3 pulls them for you.

---

## 1. Docker Compose

```sh
git clone https://github.com/EslamAlaaZaki/dawam.git
cd dawam
cp .env.example .env
```

Edit `.env`:

```sh
DAWAM_ENCRYPTION_KEY=<the key you generated>
POSTGRES_PASSWORD=<a strong password>          # for anything others can reach
DAWAM_ADMIN_EMAIL=admin@example.com            # first start only
DAWAM_ADMIN_PASSWORD=<a long password>
DAWAM_PUBLIC_URL=http://<host>:8000            # the address users open; used in emailed links
SEARXNG_SECRET=<a long random string>          # the searxng service's key: openssl rand -hex 32
# DAWAM_PORT=8000                              # host port, if 8000 is taken
```

Start it:

```sh
docker compose up -d --build
docker compose ps                  # wait until app, web and edge are "healthy"
curl http://localhost:8000/readyz  # 200 once the database is migrated
```

Open `http://localhost:8000` (or your `DAWAM_PORT`) and sign in.

**Web search:** to let the assistant search the web through the bundled `searxng`
service, go to **Admin > Web access**, add a SearXNG provider with base URL
`http://searxng:8080`, test it, and pick it as the provider. It counts as internal,
so internal-only Workspaces can use it too.

SearXNG depends on public search engines (Google, Brave, DuckDuckGo and others) that can block it with rate limits or CAPTCHAs. Register a second provider (LangSearch, with an API key) in the same place and DAWAM falls back to it when SearXNG cannot answer. Internal-only Workspaces never use it.

Day-to-day commands:

```sh
docker compose logs -f app worker web edge   # JSON log lines
docker compose restart worker
docker compose down                          # stop; data stays in the volumes
docker compose --profile ollama up -d        # also start the optional local LLM server
```

Data lives in two volumes: `db-data` (PostgreSQL) and `file-data` (uploads). Never
run `docker compose down -v` on an installation you want to keep. For backups, see
[`docs/operations.md`](../docs/operations.md).

**Upgrade:** take a backup, then run `git pull && docker compose up -d --build`.
Migrations run when `app` starts, and `/readyz` returns 200 once they are done.

**External PostgreSQL:** set `DAWAM_DATABASE_URL` in `.env`
(`postgresql+psycopg://user:pass@host:5432/db`). That database needs the `vector`
extension ([method 4](#4-local-run-without-docker), step 1). The `db` service still
starts, but nothing uses it.

**Subnet clash:** the Compose network uses `172.30.126.0/24` so that `edge` can have a
fixed address. If this clashes with your network, change three things in
`compose.yaml` together: the subnet, `edge`'s `ipv4_address` and
`DAWAM_FORWARDED_ALLOW_IPS`.

**HTTPS is off.** DAWAM serves plain HTTP. It only sends HSTS and marks cookies
`Secure` when a trusted TLS proxy reports an HTTPS request, and none is trusted
unless you set `DAWAM_TRUSTED_PROXY_CIDRS`. To make sure HSTS is never sent, keep
that unset and set `DAWAM_HSTS_MAX_AGE_SECONDS=0` in `.env`.

## 2. All-in-one Compose file

[`docker-compose.yaml`](docker-compose.yaml) runs every component from
[Components](#components) in one file, with HTTPS disabled (HSTS off, no trusted TLS
proxy):

| Service | Port on the host | Notes |
|---------|------------------|-------|
| `edge`, `web`, `app`, `worker`, `db` | `DAWAM_PORT` (8000) | DAWAM itself, as in method 1, with `DAWAM_WORKERS` (default 2) worker replicas |
| `ollama` | none | Local LLM server, always on |
| `ollama-pull` | none | Runs once at start, pulls `OLLAMA_MODELS`, then exits |
| `mailpit` | `127.0.0.1:8025` (`MAILPIT_PORT`) | SMTP server that catches every email; read them in its web UI |
| `searxng` | none | Web search for the assistant, reached as `http://searxng:8080` |
| `source-db` | none | Demo PostgreSQL source with a small `sales` schema (profile `demo`) |

1. Set up `.env` in the repository root as in method 1 (encryption key, first admin,
   `SEARXNG_SECRET`: a long random string, e.g. `openssl rand -hex 32`). Optionally add:

   ```sh
   OLLAMA_MODELS=qwen2.5:7b nomic-embed-text   # default: a chat model + an embedding model
   # OLLAMA_MODELS=qwen2.5:1.5b nomic-embed-text   # smaller machines (weaker answers)
   OLLAMA_CONTEXT_LENGTH=32768                  # default; 16384 on machines with little RAM
   ```

   Ollama's own default context is small, and it cuts longer prompts without an
   error, so the model forgets the start of long conversations. `OLLAMA_CONTEXT_LENGTH`
   raises it; full agent mode needs at least 32k tokens.

2. Start everything from the repository root, adding `--profile demo` if you want the
   demo source database. Use the same `-f` and `--env-file` in every later command:

   ```sh
   docker compose -f deploy/docker-compose.yaml --env-file .env --profile demo up -d --build
   docker compose -f deploy/docker-compose.yaml --env-file .env logs -f ollama-pull   # model download
   ```

3. Open `http://localhost:8000`, sign in as the first admin, and connect the
   components. They can't be wired up automatically, because they are stored
   (encrypted) by the app:

   - **Admin > Language models:** add a provider of type *OpenAI-compatible*, base URL
     `http://ollama:11434/v1`, no API key, then add the models `qwen2.5:7b` and
     `nomic-embed-text` on it. For `qwen2.5:7b`, enter the same value as
     `OLLAMA_CONTEXT_LENGTH` (32768 by default) as its context window: Ollama doesn't
     report it, and without it the agent never trims long conversations. Then click
     **Test connection** on each model.
   - **Admin > Roles, budgets & usage:** give `qwen2.5:7b` the `agent` (and `light`)
     role and `nomic-embed-text` the `embedding` role.
   - **Admin > Email:** host `mailpit`, port `1025`, no TLS, no login. Emails appear
     at `http://localhost:8025`. For real delivery, enter your own SMTP server instead.
   - **Admin > Web access:** add a SearXNG provider with base URL
     `http://searxng:8080`, test it, and pick it as the provider. It counts as
     internal, so internal-only Workspaces can use it too.
   - **Demo source** (with `--profile demo`): in a Workspace, add a PostgreSQL
     Connection: host `source-db`, port `5432`, database `shop`, user `reader`,
     password `reader`.

GPU: uncomment the `deploy:` block under `ollama` (needs the NVIDIA Container
Toolkit). The volumes are the same as in method 1 (the project is named `dawam` in
both), so you can switch between the two files without losing data. Don't run both
at the same time.

## Document AI: CPU vs GPU

The `worker` reads uploaded PDF, DOCX and XLSX files with Docling (layout and table
models) and the scanned pages of PDFs with RapidOCR (English and Arabic). These models
run on the CPU by default. The backend image is built for one of two accelerators, with
the `ACCELERATOR` build arg:

| Build | Contains | Use it when |
|-------|----------|-------------|
| `cpu` (default) | CPU torch + `onnxruntime`, no NVIDIA packages | Any host |
| `cuda` | CUDA torch + `onnxruntime-gpu` (about 12 GB, against about 4 GB for `cpu`) | The host has an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |

The `cuda` build uses CUDA 13, so the host needs an NVIDIA driver 580 or newer. The
driver stays on the host, never in the image. On Windows, use Docker Desktop with the
WSL2 backend and the normal Windows NVIDIA driver; no Container Toolkit install is needed.

Both builds download the models into the image (`/opt/dawam/models`), so the worker
never downloads anything at runtime and indexing works on a host without internet.

```sh
docker build .                                  # cpu
docker build --build-arg ACCELERATOR=cuda .     # cuda
```

With Compose, add [`docker-compose.gpu.yaml`](docker-compose.gpu.yaml) after the main
file: it builds the `cuda` image and gives the `worker` the GPU.

```sh
docker compose -f compose.yaml -f deploy/docker-compose.gpu.yaml up -d --build
# or, with the all-in-one file:
docker compose -f deploy/docker-compose.yaml -f deploy/docker-compose.gpu.yaml --env-file .env up -d --build
```

Use the same `-f` files in every later command. Check that the worker sees the GPU
for both engines: the first line should be `cuda` (Docling, through torch; `cpu` means
it does not see the GPU) and the second should list `CUDAExecutionProvider` (RapidOCR,
through ONNX Runtime):

```sh
docker compose -f compose.yaml -f deploy/docker-compose.gpu.yaml exec worker python -c "from dawam.modules.files.internal.accelerator import accelerator_device; import onnxruntime; print(accelerator_device('auto')); print(onnxruntime.get_available_providers())"
```

ONNX Runtime may log `No registered plugin EP device found for 'CUDAExecutionProvider'`;
it is harmless, and the OCR models still run on the GPU.

The worker also logs the device when it first parses a document
(`... runs on cuda (document_accelerator=auto)`). `DAWAM_DOCUMENT_ACCELERATOR` picks the
device: `auto` (default: the GPU when one is visible, else the CPU), `cpu`, or `cuda`
(falls back to the CPU, with a warning, when no GPU is visible). The `cuda` image also
runs without a GPU, on the CPU.

## 3. Local development

Runs each part from source, with hot reload. `next dev` forwards `/api`, `/healthz`
and `/readyz` to the API itself, so you need no proxy.

**Database** (once):

```sh
docker run -d --name dawam-dev-db -p 5432:5432 \
  -e POSTGRES_USER=dawam -e POSTGRES_PASSWORD=dawam pgvector/pgvector:pg16
```

**Backend** (terminals 1 and 2):

```sh
cd backend
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Create `backend/.env`. The app reads `.env` from its working directory. Reuse the same
key on every run:

```sh
DAWAM_DATABASE_URL=postgresql+psycopg://dawam:dawam@localhost:5432/dawam
DAWAM_ENCRYPTION_KEY=<the key you generated>
DAWAM_ADMIN_EMAIL=admin@example.com
DAWAM_ADMIN_PASSWORD=<a long password>
DAWAM_PUBLIC_URL=http://localhost:3000
```

```sh
python -m dawam serve        # terminal 1: API on http://localhost:8000, migrates on start
python -m dawam worker       # terminal 2: background worker
```

**Frontend** (terminal 3):

```sh
cd frontend
npm ci
npm run dev                  # http://localhost:3000
```

Open `http://localhost:3000`. If the API runs elsewhere, set `DAWAM_DEV_API_URL`
(default `http://localhost:8000`). After changing the API, regenerate the client with
`scripts/generate-api-client.sh`.

## 4. Local run without Docker

Runs the built app (no hot reload) on your own machine, with nginx as the entry proxy
just as in Compose.

1. **PostgreSQL 16 with pgvector.** Install PostgreSQL 16 and the pgvector package
   (for example `postgresql-16-pgvector` on Debian/Ubuntu, or `brew install pgvector`
   on macOS). Then, as a superuser:

   ```sql
   CREATE ROLE dawam LOGIN PASSWORD 'change-me';
   CREATE DATABASE dawam OWNER dawam;
   \c dawam
   CREATE EXTENSION IF NOT EXISTS vector;   -- a migration needs it; only a superuser can create it
   ```

2. **Backend:**

   ```sh
   python3.12 -m venv .venv-run
   .venv-run/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # Linux: else pip picks the CUDA build
   .venv-run/bin/pip install "./backend[ocr-cpu]"   # Windows: .venv-run\Scripts\pip; use "./backend[ocr-cpu,s3]" for S3 storage
   ```

   Copy [`host/dawam.env.example`](host/dawam.env.example) to `.env` in the directory
   you will start the app from, and fill it in. Use a `DAWAM_STORAGE_PATH` you can
   write to, for example `./data/files`. Then, from that directory:

   ```sh
   .venv-run/bin/python -m dawam serve --host 127.0.0.1 --port 8000
   .venv-run/bin/python -m dawam worker          # in a second terminal
   ```

3. **Frontend.** It is a standalone server, which `next start` cannot run:

   ```sh
   cd frontend
   npm ci && npm run build
   cp -r .next/static .next/standalone/.next/
   PORT=3000 HOSTNAME=127.0.0.1 NODE_ENV=production node .next/standalone/server.js
   ```

   On Windows PowerShell: `$env:PORT=3000; $env:HOSTNAME="127.0.0.1"; $env:NODE_ENV="production"; node .next\standalone\server.js`.

4. **Entry proxy:** include [`host/nginx.conf`](host/nginx.conf) in your nginx
   configuration (it listens on `127.0.0.1:8080`), then reload nginx.

5. Open `http://localhost:8080` and check `curl http://localhost:8080/readyz`.

Without nginx, the pages still load at `http://localhost:3000`, but every API call
fails because the standalone server does not forward `/api`.

## 5. Linux server without Docker (systemd)

Method 4 made permanent. The three services run under systemd as an unprivileged
`dawam` user and listen on `127.0.0.1` only. nginx is the only part others can reach.
The commands are for Debian/Ubuntu; adapt package names for other distributions.

1. **Packages:** PostgreSQL 16 + pgvector, Python 3.12 (with `venv`), Node.js 22.12+
   and nginx. Prepare the database as in method 4, step 1, with a strong password.

2. **User and directories:**

   ```sh
   sudo useradd --system --home /var/lib/dawam --shell /usr/sbin/nologin dawam
   sudo mkdir -p /opt/dawam /var/lib/dawam/files /etc/dawam
   sudo chown dawam:dawam /var/lib/dawam /var/lib/dawam/files
   ```

3. **Backend** (from a checkout of the repository):

   ```sh
   sudo python3.12 -m venv /opt/dawam/venv
   sudo /opt/dawam/venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
   sudo /opt/dawam/venv/bin/pip install "./backend[ocr-cpu]"   # ocr-gpu instead, with CUDA torch, for an NVIDIA GPU
   ```

4. **Frontend:**

   ```sh
   (cd frontend && npm ci && npm run build)
   sudo rm -rf /opt/dawam/web
   sudo cp -r frontend/.next/standalone /opt/dawam/web
   sudo cp -r frontend/.next/static /opt/dawam/web/.next/static
   ```

5. **Settings:**

   ```sh
   sudo cp deploy/host/dawam.env.example /etc/dawam/dawam.env
   sudo chown root:dawam /etc/dawam/dawam.env && sudo chmod 640 /etc/dawam/dawam.env
   sudoedit /etc/dawam/dawam.env   # key, database URL, DAWAM_PUBLIC_URL=http://<server>, first admin
   ```

6. **Services:**

   ```sh
   sudo cp deploy/host/systemd/dawam-*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now dawam-app dawam-worker dawam-web
   systemctl status dawam-app
   journalctl -u dawam-app -f            # JSON log lines
   curl http://127.0.0.1:8000/readyz
   ```

7. **nginx:** copy [`host/nginx.conf`](host/nginx.conf) to
   `/etc/nginx/conf.d/dawam.conf`, change `listen 127.0.0.1:8080;` to `listen 80;`,
   then run `sudo nginx -t && sudo systemctl reload nginx`.

8. Open `http://<server>`. Once you have signed in, remove `DAWAM_ADMIN_PASSWORD`
   from `/etc/dawam/dawam.env`.

**Upgrade:** take a database backup first, because migrations run when `dawam-app`
starts. Then pull the new code, repeat steps 3 and 4, and run
`sudo systemctl restart dawam-app dawam-worker dawam-web`.

**Backups:** `pg_dump` the database and archive `/var/lib/dawam/files` (or rely on
your S3 bucket's versioning). Back up `/etc/dawam/dawam.env` separately, because it
holds the encryption key.

---

## Checklist before others use it

- [ ] `DAWAM_ENCRYPTION_KEY` was generated for this installation and is backed up separately from the data.
- [ ] `POSTGRES_PASSWORD` (or the password in `DAWAM_DATABASE_URL`) is no longer `dawam`.
- [ ] `DAWAM_PUBLIC_URL` is the address users actually open.
- [ ] Only the entry proxy can be reached from the network; app, web and PostgreSQL cannot.
- [ ] `DAWAM_ADMIN_PASSWORD` was removed after the first admin signed in.
- [ ] Email is set up in Admin > Email, so password reset links reach users.
- [ ] Database and file backups are scheduled, and a restore has been tested ([`docs/operations.md`](../docs/operations.md)).
- [ ] Your monitoring watches `GET /readyz`.
