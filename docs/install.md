# Installation Guide

DAWAM is a self-hosted application for data warehouse metadata management. This guide walks you through installing and running DAWAM using Docker Compose.

## Prerequisites

- **Docker** and **Docker Compose** (20.10+)
- **PostgreSQL 16** (managed by Compose, or an external server)
- For local LLM support: a machine with sufficient resources (see [Local LLM Models](#local-llm-models))

## Quick Start

1. **Clone the repository and navigate to it:**

   ```bash
   git clone https://github.com/EslamAlaaZaki/dawam.git
   cd dawam
   ```

2. **Generate an encryption key** (required for storing credentials):

   ```bash
   python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   ```

   Or use OpenSSL:

   ```bash
   openssl rand -base64 32
   ```

3. **Create a `.env` file** from the example:

   ```bash
   cp .env.example .env
   ```

4. **Edit `.env` and set the required variables:**

   ```bash
   # Required: paste the encryption key from step 2
   DAWAM_ENCRYPTION_KEY=<your-key-here>

   # Optional: set a strong password for the database
   POSTGRES_PASSWORD=<your-secure-password>

   # Optional: create an admin account at startup
   # DAWAM_ADMIN_EMAIL=admin@example.com
   # DAWAM_ADMIN_PASSWORD=<secure-password>
   ```

5. **Start DAWAM:**

   ```bash
   docker compose up --build
   ```

   DAWAM will be available at `http://localhost:8000` once the containers are healthy.

## Configuration

All settings are controlled via environment variables in `.env`. The `.env.example` file documents every variable; here are the most common:

### Essential Settings

- **`DAWAM_ENCRYPTION_KEY`** (required): A base64-encoded 32-byte AES-256-GCM key for encrypting stored credentials. Generate a new one for each installation.

- **`POSTGRES_USER`** and **`POSTGRES_PASSWORD`**: Credentials for the PostgreSQL database. Change `POSTGRES_PASSWORD` in any installation accessible by others.

- **`POSTGRES_DB`**: The database name (default: `dawam`).

### Database

To use an external PostgreSQL 16 server instead of the Compose container:

```bash
DAWAM_DATABASE_URL=postgresql+psycopg://user:password@host:5432/database
```

### File Storage

By default, uploaded files are stored in a Docker volume at `/var/lib/dawam/files` inside the container:

- **`DAWAM_STORAGE_BACKEND`**: `local` (default) or `s3` (requires `pip install dawam[s3]`).
- **`DAWAM_STORAGE_PATH`**: Directory for local storage (default: `/var/lib/dawam/files`).

For S3-compatible storage (MinIO, Ceph, AWS S3):

```bash
DAWAM_STORAGE_BACKEND=s3
DAWAM_S3_BUCKET=my-bucket
DAWAM_S3_ENDPOINT_URL=https://s3.example.com  # optional, for non-AWS providers
DAWAM_S3_REGION=us-east-1
DAWAM_S3_ACCESS_KEY_ID=key
DAWAM_S3_SECRET_ACCESS_KEY=secret
```

### Session & Security

- **`DAWAM_SESSION_IDLE_TIMEOUT_HOURS`**: Session expires after no requests (default: 8 hours).
- **`DAWAM_SESSION_ABSOLUTE_TIMEOUT_DAYS`**: Session expires after sign-in (default: 14 days).
- **`DAWAM_LOGIN_MAX_FAILURES`**: Account locked after N failed sign-ins (default: 5).
- **`DAWAM_LOGIN_LOCKOUT_MINUTES`**: Lock duration (default: 15 minutes).

### Public URL

Set the public address users access DAWAM at (used in email links, etc.):

```bash
DAWAM_PUBLIC_URL=https://dawam.example.com
```

Default: `http://localhost:8000`.

### HTTPS Behind a Reverse Proxy

If DAWAM runs behind a TLS-terminating proxy (nginx, Traefik, etc.), configure trust:

```bash
DAWAM_TRUSTED_PROXY_CIDRS=10.0.0.5  # comma-separated IPs or networks
DAWAM_HSTS_MAX_AGE_SECONDS=31536000  # Strict-Transport-Security max-age
```

The proxy must append to `X-Forwarded-For` and set `X-Forwarded-Proto`.

### Admin Account

Create an admin user at startup:

```bash
DAWAM_ADMIN_EMAIL=admin@example.com
DAWAM_ADMIN_PASSWORD=your-password
```

Both must be set, the email must be valid, and the password must be 10–1024 characters and not common. Once an admin exists, these variables are ignored on subsequent starts.

### Logging

- **`DAWAM_LOG_LEVEL`**: `DEBUG`, `INFO` (default), `WARNING`, or `ERROR`. Logs are JSON lines to stdout.

### Database Migrations

- **`DAWAM_RUN_MIGRATIONS_ON_STARTUP`**: Apply database migrations when the app starts (default: `true`).

## Local LLM Models

### Ollama

DAWAM includes an optional Docker Compose profile for [Ollama](https://ollama.com/), a local inference server.

1. **Start Ollama:**

   ```bash
   docker compose --profile ollama up
   ```

   Ollama will run in a separate container with persistent model storage.

2. **Pull a model:**

   ```bash
   docker exec dawam-ollama-1 ollama pull llama2
   ```

   Recommended models for DAWAM's tool-calling requirements:
   - `qwen2.5:1.5b` (1.5 billion parameters, tested in CI)
   - `llama2:7b` (7 billion, better quality)
   - `mistral:7b` (7 billion, good balance)

3. **Configure DAWAM to use Ollama:**

   In the DAWAM UI (Admin > LLM Models), create an LLM provider:
   - **Type**: `openai_compatible`
   - **Base URL**: `http://ollama:11434/v1`
   - **API Key**: Leave empty (Ollama doesn't require one)
   - **Model Name**: `qwen2.5:1.5b` (or your chosen model)

### vLLM

[vLLM](https://docs.vllm.ai/) is a fast LLM serving engine for NVIDIA GPUs.

1. **Start vLLM in a container:**

   ```bash
   docker run --gpus all -p 8001:8000 \
     -v huggingface_cache:/root/.cache/huggingface \
     vllm/vllm-openai:latest \
     --model meta-llama/Llama-2-7b-hf
   ```

2. **Configure DAWAM:**
   - **Type**: `openai_compatible`
   - **Base URL**: `http://host.docker.internal:8001/v1` (or your vLLM address)
   - **API Key**: Leave empty or set if configured
   - **Model Name**: `meta-llama/Llama-2-7b-hf`

### SGLang

[SGLang](https://github.com/hpcaitech/sglang) is another fast inference engine with structured generation support.

1. **Start SGLang:**

   ```bash
   docker run --gpus all -p 8002:8000 \
     sglang/sglang:latest \
     python -m sglang.launch_server \
     --model-path meta-llama/Llama-2-7b-hf
   ```

2. **Configure DAWAM:**
   - **Type**: `openai_compatible`
   - **Base URL**: `http://host.docker.internal:8002/v1`
   - **API Key**: Leave empty
   - **Model Name**: `meta-llama/Llama-2-7b-hf`

## Health Checks

DAWAM provides two endpoints for monitoring:

- **`GET /healthz`**: Process is up (no dependencies checked). Returns `200 OK`.
- **`GET /readyz`**: Database is reachable and migrations are at head. Returns `200 OK` or `503 Service Unavailable`.

Example:

```bash
curl http://localhost:8000/readyz
```

## Air-Gapped Installation

To run DAWAM fully offline with no internet access:

1. **Build the images on a connected machine:**

   ```bash
   docker compose build
   docker save dawam:latest | gzip > dawam.tar.gz
   docker save pgvector/pgvector:pg16 | gzip > pgvector.tar.gz
   docker save ollama/ollama | gzip > ollama.tar.gz  # optional
   ```

2. **Transfer the images to your air-gapped network** (USB drive, secure transfer, etc.).

3. **Load the images on the air-gapped machine:**

   ```bash
   docker load < dawam.tar.gz
   docker load < pgvector.tar.gz
   docker load < ollama.tar.gz  # optional
   ```

4. **Download model files on a connected machine** and transfer them:

   For Ollama:

   ```bash
   # On connected machine, pull models
   docker run -it ollama/ollama ollama pull qwen2.5:1.5b
   docker run -it ollama/ollama ollama pull llama2

   # Copy the ~/.ollama directory (on the connected machine) to the air-gapped machine
   ```

5. **On the air-gapped machine**, start DAWAM with the pre-built images and models.

## Networking

By default, DAWAM uses the subnet `172.30.126.0/24` for its Docker network. If this conflicts with your host network, edit `compose.yaml`:

```yaml
networks:
  default:
    ipam:
      config:
        - subnet: 172.30.126.0/24  # Change this
          ip_range: 172.30.126.128/25
```

Also update `DAWAM_FORWARDED_ALLOW_IPS` to match the `edge` service's new IP address.

## Troubleshooting

### "DAWAM_ENCRYPTION_KEY is not set"

Generate and set the key in `.env` (see [Quick Start](#quick-start), step 2).

### Database connection fails

Check the connection string:

```bash
docker compose logs db
```

Ensure `DAWAM_DATABASE_URL` is correct (or let Compose build it from `POSTGRES_*` variables).

### No admin account

If no admin exists and startup fails, set both `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` in `.env`, then restart.

### Services not healthy

Check logs:

```bash
docker compose logs app
docker compose logs web
docker compose logs edge
```

The `readyz` endpoint indicates readiness:

```bash
curl -v http://localhost:8000/readyz
```

### Ollama model too slow

Smaller models load faster. Start with `qwen2.5:1.5b` (1.5B parameters) instead of larger ones.

## Next Steps

- **[Operations Guide](./operations.md)** for backups, key rotation, and maintenance.
- **Spec § 6** for detailed feature documentation.
- **Admin Console**: Access at `http://localhost:8000` to configure email, LLM providers, and workspaces.
