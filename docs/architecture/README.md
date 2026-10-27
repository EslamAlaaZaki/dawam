# Architecture diagrams

Interactive, source-grounded diagrams of DAWAM, made with [Archify](https://github.com/tt-a1i/archify).
Each one is a standalone HTML file: open it in a browser, no server needed. Every box links to
the source lines it is based on, pinned to commit `6d8a783`.

| Diagram | What it answers | Type |
|---------|-----------------|------|
| [System overview](architecture-system-overview-20261027-211619/system-overview.html) | Which services, stores and external systems exist, and how a request flows through them | architecture |
| [Domain module dependencies](architecture-domain-modules-20261027-213256/domain-modules.html) | How the 22 backend modules import each other, plus the runtime hooks the composition roots wire | architecture |
| [Frontend architecture](architecture-frontend-20261027-213256/frontend.html) | Next.js routes, the sign-in guard, the API hook layer and the generated OpenAPI contract | architecture |
| [Background job lifecycle](lifecycle-background-jobs-20261027-213256/background-jobs.html) | How a job is queued, claimed, paused, retried, failed or cancelled | lifecycle |
| [Source database access paths](architecture-source-db-flows-20261027-213256/source-db-flows.html) | Every path that opens a connection to a source database, and the guards on model-written SQL | architecture |
| [AI assistant chat turn](sequence-assistant-chat-20261027-213256/assistant-chat.html) | One chat message end to end: SSE, LLM gateway, tool checks, persistence | sequence |
| [Deployment variants](architecture-deployment-variants-20261027-213256/deployment-variants.html) | `compose.yaml`, the all-in-one Compose file, the GPU override and the systemd host install | architecture |

## Reading the diagrams

- Solid arrows are runtime calls seen at call sites. Dashed arrows are configuration-dependent
  or build-time links; each diagram's cards say which.
- In the domain module diagram, arrows are static imports (only the transitive reduction is
  drawn; the cards list every import).
- Each diagram has an "Uncertain" card listing what was not traced or is inferred.

## Updating a diagram

Each folder holds `candidate.json` (the source) and the generated HTML with its check
receipts. Edit `candidate.json`, delete the old `<name>.finalize*.json` and
`<name>.browser-check.json` receipts, then re-render and re-check from the repository root:

```sh
node <archify>/bin/archify.mjs finalize <type> <folder>/candidate.json <folder>/<name>.html \
  --repo-root . --quality showcase
```

`finalize` runs validation, delivery, the source-evidence check against the pinned commit and a
real-browser check. When the code changes, update `meta.repository.revision` and the cited
line ranges in the candidate.
