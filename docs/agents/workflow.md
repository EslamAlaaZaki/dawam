# Working agreements

How the maintainer wants agents to work on DAWAM.

## Tickets run as a pipeline, without asking

Tickets run one at a time by default, end to end. Run them in parallel only when the maintainer asks; then put each implementer in its own worktree and merge in dependency order, rebasing the later branches. Don't ask before opening or merging a PR.

For each ticket:

1. The main session is a thin orchestrator. It picks the next unblocked ticket (`gh issue list`) and doesn't read code itself.
2. **Implement** with a fresh `ticket-implementer` subagent (`.claude/agents/ticket-implementer.md`). Choose its model for the ticket: `sonnet` for routine or well-specified work, `opus` only for design-heavy, security-sensitive or tricky tickets, `haiku` for one-line docs or config changes.
3. **Review** with one fresh subagent that checks both Standards and Spec: `haiku` for small diffs (under about 150 lines), `sonnet` otherwise. Never `opus`.
4. Send fixes back to the same implementer (SendMessage keeps its context). Run a second review only if the first found a real bug or a hard spec violation, and never more than two rounds.
5. Open a PR ("Closes #N"), wait for CI, fix it if red, merge with a merge commit, delete the branch, pull main.
6. Post a 2–3 line summary, then move to the next ticket.

Save tokens:
- Subagents return short reports, never file dumps.
- Implementers read only the `docs/spec.md` sections the ticket cites.
- Skip docker-compose and browser end-to-end runs unless the ticket touches infra or the frontend flow; CI covers the rest.

Stop and ask only for genuine product decisions, or for tickets labelled `needs-info` or `needs-triage`.

When the main session's context gets long (about every 3–4 tickets), or the next ticket needs a different main model, tell the maintainer to run `/clear` (and `/model X` if needed) and then say "next".

**Why:** running Opus implementers in parallel with multi-round reviews cost 0.7–1M tokens per ticket and used up the 5-hour limit in under 30 minutes.

## UI redesign exception

Tickets for the UI redesign (`DESIGN.md`) change steps 2 and 5 above:

- The implementer commits straight to `feature/ui-redesign`, with commit messages like `ui: <short description> (#N)`. No ticket branches and no PRs. Nothing is ever pushed to, merged into or opened as a PR against `main`.
- The ticket is visual-only, so no new tests. It's done when `npm run lint`, `npm test` and `npm run build` pass in `frontend/` without changing any test. The exceptions are `contrast.test.ts` and `tokens.test.ts` (palette ticket only) and tests that only assert class names or colors. If a role or text test fails, fix the UI, not the test.
- The review subagent still runs. CI runs on push. The maintainer closes the issue by hand.

## AI enhancements exception

AI enhancement tickets (label `ai`, from `docs/ai-enhancements.md`) change step 5 above.

| Feature | Label | Feature branch | Epics |
|---|---|---|---|
| AI Foundations | `feature:ai-foundations` | `feature/ai-foundations` | 20 Context budget & token cost, 27 Observability & evals |
| AI RAG | `feature:ai-rag` | `feature/ai-rag` | 21 Document ingestion, 22 Chunking & indexing, 23 Retrieval quality, 24 RAG admin settings |
| AI Safety & Platform | `feature:ai-safety-platform` | `feature/ai-safety-platform` | 28 Safety, governance & platform |
| AI Agent | `feature:ai-agent` | `feature/ai-agent` | 25 Agent tools, 26 Agent runtime & orchestration, 29 Agent memory |

- Work the features one at a time, in the table's order, unless the maintainer asks otherwise. Create a feature branch from `main` only when its work starts, after the previous feature has merged into `main`.
- Each issue's header names its Feature, Feature branch and Target branch. Cut the ticket branch from the target branch, open a PR into it, wait for CI to pass, then merge with a merge commit. Never commit directly to a feature branch, and never open a ticket PR into `main` (leftovers below are the only exception).
- "Closes #N" doesn't fire on PRs into non-default branches. Right after merging a ticket PR into its feature branch, close the issue by hand with a comment linking the PR.
- Each feature has a "Merge `feature/ai-X` into main" issue (`ready-for-human`), blocked by its epics' docs tickets. Blockers that cross features point at that merge issue; blockers within a feature point at the ticket.
- When the merge issue unblocks, sync `main` into the feature branch with a "Sync main into feature/ai-X" PR (CI must pass, merge commit), then open the feature → `main` PR. The maintainer reviews and merges it.
- If `main` changes while a feature branch is open, sync it the same way before continuing. The ruleset blocks direct pushes, so syncing always goes through a PR.
- Leftover nice-to-have or backlog tickets after their feature has merged: change only "Target branch" to `main` (keep the Feature, the Feature branch line and the feature label), and run each as a single-ticket PR into `main` that also updates `docs/ai-architecture.md` and `docs/ai-enhancements.md`.
- Tickets that write or amend an ADR: label the PR `ready-for-human` and wait for the maintainer's review before merging, even though the ruleset allows 0 approvals.
- A ruleset on `feature/ai-**` requires a PR (0 approvals, merge commits only, no force pushes) and every CI check. CI runs on every PR and on pushes to `main` and `feature/**`.

## Grilling: clear the backlog first

When interviewing the maintainer, don't ask new questions while earlier ones are unanswered. Each round, first list only the unanswered questions; ask new ones once that list is empty. Asking to clarify an ambiguous answer is fine.

## CI notes

- A backend job that fails with Docker Hub `toomanyrequests` hit the image pull rate limit; it isn't a code failure. Re-run the job.
- The full backend suite (Testcontainers for PostgreSQL and SQL Server) can take close to an hour on CI.
