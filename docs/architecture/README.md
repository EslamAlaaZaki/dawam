# Architecture diagrams

Interactive, source-grounded diagrams of DAWAM, made with [Archify](https://github.com/tt-a1i/archify).
Each one is a standalone HTML file: open it in a browser, no server needed. Every box links to
the source lines it is based on, pinned to commit `6d8a783`. Each diagram has an "Uncertain" or
"Simplified" card that separates what was verified in code from what is inferred or left out.

## System architecture

| Diagram | What it answers | Type |
|---------|-----------------|------|
| [System overview](architecture-system-overview-20261027-211619/system-overview.html) | Which services, stores and external systems exist, and how a request flows through them | architecture |
| [Domain module dependencies](architecture-domain-modules-20261027-213256/domain-modules.html) | How the 22 backend modules import each other, plus the runtime hooks the composition roots wire | architecture |
| [Background job lifecycle](lifecycle-background-jobs-20261027-213256/background-jobs.html) | How a job is queued, claimed, paused, retried, failed or cancelled | lifecycle |
| [Source database access paths](architecture-source-db-flows-20261027-213256/source-db-flows.html) | Every path that opens a connection to a source database, and the guards on model-written SQL | architecture |

## Database

| Diagram | What it answers | Type |
|---------|-----------------|------|
| [Database ERD](database/erd.html) | Every table, column, type, default, allowed value, key, index, constraint and relationship (1:1, N:1, N:M) | ERD |

The ERD is not an Archify diagram: `backend/tools/generate_erd.py` generates it from a database
migrated to head, and `backend/tests/test_erd_is_current.py` fails CI when it is stale. Regenerate it
with `python tools/generate_erd.py` in `backend/` whenever the schema changes.

## Functional architecture and use cases

| Diagram | What it answers | Type |
|---------|-----------------|------|
| [Main flow](workflow-main-flow-20261027-223614/main-flow.html) | The whole product in six steps (connect, documents, understand, KPIs, design, evaluate) and what the AI assistant does at each | workflow |
| [Use case: understand a source system](workflow-uc-source-analysis-20261027-220352/uc-source-analysis.html) | Connect or import → extract → Snapshot → analysis jobs → review, with error paths | workflow |
| [Use case: design the data warehouse](workflow-uc-dw-design-20261027-220352/uc-dw-design.html) | Staging → AI Core/Mart as a Change Set → mappings → score gate and approval → DDL | workflow |

The use cases also rely on
[Source database access paths](architecture-source-db-flows-20261027-213256/source-db-flows.html) and the
[Background job lifecycle](lifecycle-background-jobs-20261027-213256/background-jobs.html).

## AI architecture

| Diagram | What it answers | Type |
|---------|-----------------|------|
| [LLM layer](architecture-llm-layer-20261027-220352/llm-layer.html) | Model roles, RoleService, MeteredGateway (internal-only, budgets, fallbacks, usage), Gateway retries, the five adapters and plugins | architecture |
| [Agent loop](workflow-agent-loop-20261027-220352/agent-loop.html) | One iteration per model turn, tool execution, and every way a run ends (completed, tool_limit, cancelled, failed, asked) | workflow |
| [Assistant chat (tool-assisted)](sequence-assistant-chat-20261027-213256/assistant-chat.html) | Tool calling: permission checks, execution as the member, results back to the model | sequence |
| [Retrieval-assisted chat](sequence-chat-rag-20261027-220352/chat-rag.html) | Auto-retrieve and search_documents: embedding, full-text + vector ranking, quoted passages | sequence |
| [RAG pipeline](dataflow-rag-pipeline-20261027-220352/rag-pipeline.html) | Upload → Docling / OCR → chunking → full-text and pgvector index → search | dataflow |
| [Assistant background task](sequence-assistant-job-20261027-220352/assistant-job.html) | start_job → worker run with checkpoints → ask_member pause → one Change Set | sequence |

The [system overview](architecture-system-overview-20261027-211619/system-overview.html) shows where the
agentic layer sits among the other services.

## Reading the diagrams

- Solid arrows are runtime calls seen at call sites. Dashed arrows are asynchronous,
  configuration-dependent or build-time links; each diagram's cards say which.
- In the domain module diagram, arrows are static imports (only the transitive reduction is
  drawn; the cards list every import).

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
