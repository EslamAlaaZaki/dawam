# DAWAM

Product spec: `docs/spec.md`. It is large (~130 KB): grep for the relevant heading and read only that section, never the whole file.

AI docs: `docs/ai-architecture.md` (~50 KB, how the AI side works today) and `docs/ai-enhancements.md` (~34 KB, the AI roadmap). Open them only when the task is about the AI side, and then the same way: grep for the heading, read only that section.

## Database ERD (permanent rule)

`docs/architecture/database/erd.html` documents every table, column, type, nullability, default, enum value, key, index, constraint and relationship. It is generated, so edit the generator, not the HTML.

Every task that changes the schema regenerates the ERD in that same task. The schema changes when you add or edit a migration, or edit a module's `tables.py` (its docstrings become the ERD's descriptions). In `backend/`:

```sh
python tools/generate_erd.py    # migrates a throwaway PostgreSQL (Docker), rewrites the ERD
```

Commit `erd.html` with the schema change. `tests/test_erd_is_current.py` fails CI when the ERD is stale. Details: `.claude/rules/database-erd.md`.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on EslamAlaaZaki/dawam (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Workflow

Tickets run as an implement → review → PR → CI → merge pipeline, one at a time, without asking. Implementers use the `ticket-implementer` agent (`.claude/agents/`). Read `docs/agents/workflow.md` before working tickets or grilling the maintainer.

## UI rules
- UI ONLY: change how things look and are arranged, never what the app does.
  No new features, no new API calls, no backend changes. Keep all routes, forms and behavior.
- Follow DESIGN.md (Precision minimal). Use the frontend-design skill within that direction.
- Use shadcn/ui via the shadcn MCP, keeping the same behavior as the component it replaces.
- Use DESIGN.md tokens only. No raw hex colors, no arbitrary values like p-[13px].
- Table/column names and data types always in the mono font.
- Gradients and the ✦ mark only on the AI assistant.
- Change only the files the task needs. Don't take screenshots. Don't refactor logic.
- If a design idea would need a new feature or data, skip it and mention it in one line.
- When done, give a short list of changed files. No long explanations.

## Git rules for the UI redesign
- All UI work is committed on the branch feature/ui-redesign. Check you're on it before editing.
- Never commit to, push to, merge into or open a PR against main.
- Don't create other branches unless I ask.
- Commit message format: "ui: <short description> (#<issue number>)".

