# DAWAM Documentation

This directory contains documentation for DAWAM, a self-hosted data warehouse metadata management platform.

## Quick Links

- **[Deployment Guide](../deploy/README.md)** — Every way to run DAWAM (Docker Compose, all-in-one, local, systemd) and its components.
- **[Installation Guide](./install.md)** — Get DAWAM up and running with Docker Compose, including local LLM setup (Ollama, vLLM, SGLang).
- **[Configuration Reference](./configuration.md)** — Every `.env` setting with its default, and how each Compose service gets it.
- **[Operations Guide](./operations.md)** — Run DAWAM in production: backups, monitoring, troubleshooting, and maintenance.
- **[Feature Reference](./reference.md)** — Accounts, security controls, email, Workspaces and files, with their settings and API endpoints.
- **[Contributing](../CONTRIBUTING.md)** — Development setup, conventions, tests and pull requests.
- **[Specification](./spec.md)** — Full product specification (§ 6 covers features and modules).
- **[AI Architecture](./ai-architecture.md)** — How the AI side works today: gateway, agent loop, tools, RAG and safety.
- **[AI Enhancements](./ai-enhancements.md)** — The AI roadmap, with the status of each item.
- **[Architecture diagrams](./architecture/)** — Interactive, source-linked diagrams: system overview, backend modules, background jobs, source database access, functional use cases and the AI architecture.
- **[Architecture Decision Records](./adr/)** — Design decisions and their rationale.
- **[Agent working agreements](./agents/)** — Issue tracker, triage labels, domain docs and the ticket workflow for coding agents.

## For Self-Hosters

Start here:

1. Read **[Installation Guide](./install.md)** to set up DAWAM.
2. Follow **[Operations Guide](./operations.md)** for backups and maintenance.
3. Refer to **[Specification § 6](./spec.md)** for feature details.

## Air-Gapped Deployments

See [Air-Gapped Installation](./install.md#air-gapped-installation) for running DAWAM fully offline with no internet access.

## Local LLM Models

DAWAM supports three popular open-source inference engines:

- **[Ollama](./install.md#ollama)** — Easiest setup, works on CPU or GPU.
- **[vLLM](./install.md#vllm)** — Fast GPU inference, NVIDIA GPUs.
- **[SGLang](./install.md#sglang)** — Structured generation, NVIDIA GPUs.

Configuration examples and recommended models are in [Installation Guide § Local LLM Models](./install.md#local-llm-models).

## Health & Monitoring

DAWAM provides two HTTP endpoints:

- `GET /healthz` — Process is alive (returns 200 if up).
- `GET /readyz` — Database is ready and migrations are at head (returns 200 if ready, 503 if not).

See [Monitoring & Logging](./operations.md#monitoring--logging) in the Operations Guide.

## Configuration

All settings are environment variables in `.env`. The `.env.example` file in the repo root documents every variable.

Key settings:
- `DAWAM_ENCRYPTION_KEY` (required) — Base64-encoded 32-byte key for credential encryption.
- `POSTGRES_PASSWORD` — Database password (change for production).
- `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` — Create an admin at startup.
- `DAWAM_PUBLIC_URL` — Public address (used in email links).

See the [Configuration Reference](./configuration.md) for every option.

## Known Limitations

**Not yet implemented:**

- **Key Rotation** — There is no automated tool to rotate `DAWAM_ENCRYPTION_KEY`; changing it means re-entering stored credentials. See [Encryption Key Rotation](./operations.md#encryption-key-rotation) for details.
- **Prometheus Metrics** — Health endpoints exist (`/healthz`, `/readyz`), but no `/metrics` or Prometheus exporter yet.

## Support

- **Issues & Bug Reports**: [GitHub Issues](https://github.com/EslamAlaaZaki/dawam/issues)
- **Architecture Decisions**: See `adr/` for design docs.
- **Feature Spec**: See `spec.md` (very large; grep for your topic).

## File Structure

```
docs/
├── README.md                 # This file
├── install.md                # Installation & setup guide
├── configuration.md          # Every .env setting and its default
├── operations.md             # Backups, monitoring, maintenance
├── reference.md              # Accounts, security, email, Workspaces, files
├── spec.md                   # Full product specification
├── ai-architecture.md        # How the AI side works today
├── ai-enhancements.md        # AI roadmap
├── agents/                   # Working agreements for coding agents
└── adr/                      # Architecture Decision Records
    ├── 0001-*.md
    ├── 0002-*.md
    └── ...
```
