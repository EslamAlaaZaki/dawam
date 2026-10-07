# DAWAM Documentation

This directory contains documentation for DAWAM, a self-hosted data warehouse metadata management platform.

## Quick Links

- **[Installation Guide](./install.md)** — Get DAWAM up and running with Docker Compose, including local LLM setup (Ollama, vLLM, SGLang).
- **[Operations Guide](./operations.md)** — Run DAWAM in production: backups, monitoring, troubleshooting, and maintenance.
- **[Specification](./spec.md)** — Full product specification (§ 6 covers features and modules).
- **[Architecture Decision Records](./adr/)** — Design decisions and their rationale.

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

See [Configuration](./install.md#configuration) in the Installation Guide for all options.

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
├── install.md               # Installation & setup guide
├── operations.md            # Backups, monitoring, maintenance
├── spec.md                  # Full product specification
└── adr/                     # Architecture Decision Records
    ├── 0001-*.md
    ├── 0002-*.md
    └── ...
```
