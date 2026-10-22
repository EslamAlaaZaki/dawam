# DAWAM UI roadmap

Make DAWAM look like a modern SaaS (the "Precision minimal" direction in `DESIGN.md`) with a signature look for the AI assistant.

**UI only.** No new features, no new APIs, no backend changes. Everything the app does today keeps working exactly the same; only how it looks and how it's arranged changes.

- **Stack:** Next.js + Tailwind CSS
- **Flow:** Setup → Plan (grill, spec, GitHub tickets) → Build one ticket per session on the feature branch → Final cleanup → Merge the feature branch into `main` once
- **Branching:** all work happens on `feature/ui-redesign`. Nothing is merged into `main` until the very end.
- **Effort:** about 1 week part-time

---

## 1. Setup (once, about 15 minutes)

### 1.1 Install the design tools

```bash
# Skill: design quality for UI (Anthropic, official)
/plugin marketplace add anthropics/claude-code
/plugin install frontend-design@claude-code-plugins

# Skill: one review pass at the end (Vercel's UI guidelines)
npx skills add vercel-labs/agent-skills@web-design-guidelines

# MCP: lets Claude install real, current shadcn/ui components
npx shadcn@latest mcp init --client claude
```

Restart Claude Code and run `/mcp` to check that shadcn is connected.

### 1.2 Check Matt Pocock's skills

They're already installed. Two quick checks:
- `/setup-matt-pocock-skills` has been run **in this repo**, with GitHub as the tracker (`gh auth login` done).
- Type `/` and confirm you see `/to-spec` and `/to-tickets`. On an older version they're called `/to-prd` and `/to-issues`; use those names instead.

### 1.3 Add the design files

Put `DESIGN.md` and `UI-ROADMAP.md` in the repo root, then add this to the end of `CLAUDE.md`:

```markdown
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
```

### 1.4 Create the feature branch

```bash
git checkout main
git pull
git checkout -b feature/ui-redesign
git push -u origin feature/ui-redesign
```

Commit `DESIGN.md`, `UI-ROADMAP.md` and the `CLAUDE.md` changes as the first commit on this branch:

```bash
git add DESIGN.md UI-ROADMAP.md CLAUDE.md
git commit -m "ui: add design system and roadmap"
git push
```

Optional but recommended: in GitHub, add a branch protection rule on `main` that requires a pull request. Then nothing can reach `main` by accident.

### Your toolbox

| Tool | What it's for | When |
|---|---|---|
| `/grill-with-docs` | Interviews you and records decisions in docs | Planning, once |
| `/to-spec` | Turns the decisions into a spec on GitHub | Planning, once |
| `/to-tickets` | Splits the spec into small GitHub issues | Planning, once |
| `/implement` | Builds one ticket | Every build session |
| frontend-design | Better layouts and details | Named in UI tickets |
| shadcn MCP | Adds the right components correctly | Any ticket needing a component |
| web-design-guidelines | Catches UX and accessibility mistakes | Final cleanup only |

---

## 2. Plan: create all the tasks (one session, about 1 hour)

Use `/model opus`. Run the three commands in the same session, in order.

### 2.1 Grill

```
/grill-with-docs I want to restyle DAWAM's UI to look like a modern SaaS.
Read DESIGN.md (the design system, direction "Precision minimal") and UI-ROADMAP.md
(section 4 "Scope" is the plan). Grill me on scope, order, the page list and what "done"
means for each step, so we can turn this into small tickets.
Hard constraint: UI only. No new features, no new API calls, no backend changes; every
existing behavior stays the same. Small project, solo developer, no screenshots.
```

Keep your answers short. Have these ready:
- File paths of the main pages (workspace page, schema, mappings, change set review, assistant)
- Which pages matter most for demos (they go first)
- Any page you want to leave as it is

### 2.2 Spec

```
/to-spec
```

Read the spec on GitHub and fix anything wrong now, especially anything that sounds like a new feature. Changing one spec is cheaper than changing 20 tickets.

### 2.3 Tickets

```
/to-tickets Keep tickets small: one page or one shared component per ticket.
Every ticket is visual-only: same behavior, routes, APIs and data.
All tickets are built on the branch feature/ui-redesign; no ticket gets its own branch or PR,
and nothing is merged into main until the whole redesign is done.
Each ticket must name the files to change, link the DESIGN.md sections to follow, say which
skill to use (frontend-design for UI work), and have a short "done when" checklist.
No tickets for new features, screenshots, audits or tests.
```

**Expected result:** about 12 to 20 GitHub issues in dependency order, roughly matching the Scope below.

---

## 3. Build: one ticket per session

For each ticket, in dependency order:

1. Make sure you're on the feature branch: `git checkout feature/ui-redesign`
2. `/clear`
3. ```
   /implement #<issue number> This is a visual-only change: skip writing tests and keep all
   behavior the same. Work on feature/ui-redesign; don't create a new branch, don't open a PR,
   don't touch main.
   ```
4. Check the page yourself in the browser (`npm run dev`) and click through it once to make sure everything still works.
5. If something looks off, describe it in one sentence, or paste one screenshot yourself.
6. Commit and push to the feature branch:
   ```bash
   git add -A
   git commit -m "ui: <short description> (#<issue number>)"
   git push
   ```
7. Close the issue manually on GitHub, with a comment like "Done on feature/ui-redesign". (GitHub only auto-closes issues from commits that reach `main`, so tickets won't close by themselves on the feature branch.)

**Keep the feature branch up to date.** If `main` gets other changes during the redesign, bring them in about once a week so the final merge stays easy:

```bash
git checkout feature/ui-redesign
git fetch origin
git merge origin/main
git push
```

Use `/model opus` for the app shell and AI assistant tickets, `/model sonnet` for everything else.

---

## 4. Scope (input for the grilling session)

These 8 steps are what the tickets should cover. Each prompt also works on its own if you ever want to run a step directly (add "This is GitHub issue #<number>.").

### Step 1: Theme and fonts

```
Follow DESIGN.md sections 2 to 4. UI only.
- Add the color tokens to globals.css as shadcn variables (light theme), plus our extra tokens
  (surface, border-strong, subtle-foreground, primary-subtle, success/warning/danger/info with
  -subtle, ai-from/ai-to/ai-subtle), and expose them as Tailwind classes.
- Load Inter and JetBrains Mono with next/font in app/layout.tsx as font-sans and font-mono.
Don't change any pages.
```
**Done when:** the app works as before, with the new font and colors.

### Step 2: App shell (biggest visual win)

```
Use the frontend-design skill. Follow DESIGN.md section 6. UI only.
Restyle the existing workspace layout:
- Sidebar: DAWAM logo, then the existing links grouped under headings
  (Overview / Sources / Understand / Design / Govern, then Assistant with ✦), lucide icons,
  active state with primary-subtle. Don't add or remove links.
- Slim top bar with breadcrumbs and the existing controls.
- Restyle the admin sidebar the same way, with an "Admin" label.
Keep all routes, links and behavior the same.
```
**Done when:** every page shows the new shell and every link still goes to the same place.

### Step 3: Shared building blocks

```
Follow DESIGN.md sections 7 and 10. UI only.
Create three presentational components:
1. PageHeader: title, description, slot for the page's existing action buttons.
2. EmptyState: icon, title, one sentence, optional existing action.
3. A shared table style on shadcn Table (sticky header, 40px rows, hover, mono option,
   right-aligned numbers) that keeps each table's existing features.
Apply them to the members page only, as the example, keeping its behavior identical.
```
**Done when:** the members page looks clean and works exactly as before.

### Step 4: Workspace page

```
Use the frontend-design skill. Follow DESIGN.md. UI only.
Restyle the workspace page at <file path> with the Overview pattern:
PageHeader with the existing actions, the information the page already shows arranged as
stat cards and clean lists, status values as badges, lifecycle progress shown as a progress
bar if the page already shows it. Don't add new data or actions.
```

### Step 5: Schema browser

```
Use the frontend-design skill. Follow DESIGN.md. UI only.
Restyle the schema page at <file path> with the Explorer pattern:
the existing table list on the left, the selected table's existing information on the right
(name in mono, existing details as badges, columns in the shared table style with PK/FK/PII
badges). Use tabs only to organise information the page already shows. Don't add new data.
```

### Step 6: AI assistant signature look

Add the components first (shadcn-based, made for Next.js; used only for their look):

```bash
npx ai-elements@latest add conversation message prompt-input reasoning tool shimmer code-block
```

```
Use the frontend-design skill. Follow DESIGN.md section 9. UI only.
Restyle the existing AI assistant at <file path> using the AI Elements components we just added:
- Header with ✦ and a 1px gradient border (ai-gradient).
- Assistant messages on the left with ✦ and ai-subtle tint, user messages on the right.
- Clean markdown, SQL in code blocks, shimmer on the existing loading state.
- Reasoning and tool steps only if our API already sends them.
- Existing change-set results shown as a styled card linking to the existing review page.
- Restyled prompt input with the existing send button.
Keep the existing data flow, API calls and behavior exactly the same.
```
**Done when:** the assistant works as before but looks clearly special next to the calm rest of the app.

### Step 7: Remaining pages

One ticket per page: sources, snapshots, model, mappings, lineage, docs, KPIs, members, change set review, account, admin pages, auth pages.

```
Follow DESIGN.md. UI only. Restyle the <page name> page at <file path> using the matching
pattern from section 6. Reuse PageHeader, EmptyState and the shared table style.
Change only this page; keep all behavior the same.
```
For change set review, add: "If the page already shows before and after, lay them out side by side, like a GitHub pull request review."

### Step 8: Final cleanup

```
Use the web-design-guidelines skill to review components/ and app/ against DESIGN.md. UI only.
Fix: hard-coded colors, arbitrary Tailwind values, inconsistent spacing, icon buttons without
aria-label, and inconsistent button label casing. Don't change any behavior.
Give me a short list of what you fixed.
```

---

## 5. Finish: merge the feature branch into main (once)

When all tickets are closed:

1. Bring in the latest `main` and make sure the app builds:
   ```bash
   git checkout feature/ui-redesign
   git fetch origin
   git merge origin/main
   npm run build
   git push
   ```
2. Click through the main flows once in the browser (login, workspace page, schema, mappings, change set review, assistant, admin).
3. Open one pull request from `feature/ui-redesign` into `main`:
   ```bash
   gh pr create --base main --head feature/ui-redesign --title "UI redesign: Precision minimal" --body "Visual-only redesign following DESIGN.md. No behavior, API or backend changes."
   ```
4. Review the PR yourself, then merge it on GitHub. A squash merge gives `main` one clean commit for the whole redesign; a normal merge keeps every ticket's commit in history. Either is fine.

---

## 6. Token-saving rules

- Plan once. Afterwards, adjust single tickets instead of re-planning everything.
- One ticket per session, then `/clear`.
- Always give exact file paths. Never ask Claude to "look through the app".
- Check results yourself in the browser; don't ask Claude to screenshot or audit.
- Use plan mode only for the app shell and AI assistant tickets.
- If Claude adds something new, say: "UI only. Revert the new behavior and keep just the visual changes."
