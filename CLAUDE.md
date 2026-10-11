# DAWAM

Product spec: `docs/spec.md`. It is large (~130 KB): grep for the relevant heading and read only that section, never the whole file.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on EslamAlaaZaki/dawam (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Workflow

Tickets run as an implement → review → PR → CI → merge pipeline, one at a time, without asking. Implementers use the `ticket-implementer` agent (`.claude/agents/`). Read `docs/agents/workflow.md` before working tickets or grilling the maintainer.
