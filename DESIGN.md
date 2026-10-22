# DAWAM design system: Precision minimal

This file is the single source of truth for how DAWAM looks.
Claude Code must read it before any UI work and follow it over its own taste.

## 0. Scope rule: UI only

This redesign changes **how things look and are arranged, never what the app does.**

- No new features, buttons that do new things, shortcuts, settings or toggles.
- No new API calls, endpoints, database changes or backend changes.
- Keep every existing behavior, route, form field, validation and permission exactly as it is.
- Rearranging existing information (new layout, grouping, tabs over content that's already on the page) is fine. Inventing new information is not.
- If something in this file would need a new feature or new data, skip it and mention it in one line.

## 1. Direction

**Personality:** calm, precise, technical, trustworthy. A serious engineering tool that data teams spend 8 hours a day in.

**References:**
- Linear: app shell, density, restraint
- Supabase Studio: schema and table views, monospace identifiers
- Vercel dashboard: clean overview pages and empty states

**The one exception: the AI assistant.** Everything is quiet and neutral, except the assistant, which gets a signature look (section 9).

**Never:**
- Gradients, glows or glass outside the AI assistant
- More than one accent color
- Heavy shadows, thick borders, bubbly rounded cards
- Emoji in the UI
- Raw hex values or arbitrary Tailwind values (`p-[13px]`) in components: use tokens

## 2. Color tokens

Light theme only for now. Implement as CSS variables in the shadcn/ui theme format (`globals.css`), converting hex to the format the project uses (oklch or hsl).

### Neutrals

| Token | Value | Use |
|---|---|---|
| `--background` | `#FFFFFF` | Page canvas |
| `--surface` | `#FAFAFA` | Sidebar, panels, table headers |
| `--surface-raised` | `#FFFFFF` | Cards, popovers, dialogs |
| `--muted` | `#F4F4F5` | Hover rows, input backgrounds, chips |
| `--border` | `#E6E6E9` | Default hairline (1px) |
| `--border-strong` | `#D4D4D8` | Inputs, emphasized dividers |
| `--foreground` | `#16161A` | Primary text |
| `--muted-foreground` | `#5F5F6B` | Secondary text |
| `--subtle-foreground` | `#71717A` | Captions, metadata, placeholders |

### Accent (one only)

| Token | Value | Use |
|---|---|---|
| `--primary` | `#5B5BD6` | Primary buttons, links, active nav, focus ring |
| `--primary-foreground` | `#FFFFFF` | Text on primary |
| `--primary-subtle` | `#EEEEFC` | Selected rows, active nav background |

At most **one** primary button per view. Everything else is secondary (bordered) or ghost.

### Semantic

| Token | Value | Use |
|---|---|---|
| `--success` | `#16A34A` | Healthy, approved, passed |
| `--warning` | `#D97706` | Needs review, stale |
| `--danger` | `#DC2626` | Failed checks, destructive actions |
| `--info` | `#0284C7` | Running jobs, neutral notices |

Each semantic color also has a `-subtle` background (about 10% tint) for badges. Badge text uses the strong color on the subtle background.

### Data tags (fixed meaning everywhere)

| Tag | Color family |
|---|---|
| PK | Accent (indigo) |
| FK | Info (sky) |
| PII | Danger (red) |
| Failed data quality check | Warning (amber) |
| Mapped / approved | Success (green) |

### AI tokens (only for the AI assistant)

| Token | Value |
|---|---|
| `--ai-from` | `#6E56CF` (violet) |
| `--ai-to` | `#0EA5E9` (sky) |
| `--ai-gradient` | `linear-gradient(135deg, var(--ai-from), var(--ai-to))` |
| `--ai-subtle` | `#F5F3FF` |

## 3. Typography

- **UI font:** Inter, loaded with `next/font`. Fallback `system-ui, sans-serif`.
- **Mono font:** JetBrains Mono, for every table name, column name, data type, SQL snippet and ID.
- Tabular numerals (`tabular-nums`) for numbers in tables, KPIs and counts.

| Style | Size / line height | Weight | Use |
|---|---|---|---|
| Display | 30 / 36 | 600 | Big numbers on overview cards |
| H1 | 24 / 32 | 600 | Page titles |
| H2 | 18 / 28 | 600 | Section titles |
| H3 | 15 / 22 | 500 | Card titles |
| Body | 14 / 20 | 400 | Default app text |
| Small | 13 / 18 | 400 | Table cells, secondary text |
| Caption | 12 / 16 | 500 | Labels, badges, metadata |

Only weights 400, 500, 600. Sentence case everywhere ("Add source", not "Add Source").

## 4. Spacing, radius, elevation

- **Spacing:** 4px grid. Steps: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.
- **Page padding:** 24px desktop, 16px mobile.
- **Radius:** `6px` controls and badges, `10px` cards and panels, `12px` dialogs.
- **Borders over shadows.** Cards use a 1px `--border`, no shadow. Shadows only on floating layers (popovers, dropdowns, dialogs).
- **Table rows:** 40px high.

## 5. Motion

- Keep whatever transitions shadcn components already have (popovers, dialogs, sheets).
- Hover and press transitions: 120ms.
- No new animations, bouncing or animated backgrounds.

## 6. Layout

### App shell (restyle the existing one)
- **Left sidebar (240px):** DAWAM logo on top, then the existing workspace links grouped under headings:
  - Overview
  - **Sources:** sources / connections, snapshots
  - **Understand:** schema, ER diagram, data quality
  - **Design:** model, mappings, lineage
  - **Govern:** change sets, docs, KPIs, members
  - Assistant (with the ✦ mark)
- Group only links that already exist; don't add pages.
- **Top bar (48px):** breadcrumbs on the left; existing controls (user menu, notifications if they exist) on the right.
- **Admin** pages keep their own sidebar with a visible "Admin" label.

### Page patterns
| Pattern | Used by |
|---|---|
| Overview (header, stat cards, lists) | Workspace page, workspaces list, KPIs |
| Explorer (list or tree on the left, details on the right) | Schema, model |
| Table page (header + one table) | Sources, snapshots, mappings, members, all admin lists |
| Split view (before / after) | Change set review, snapshot diffs |
| Settings (form sections in cards) | Account, AI settings, admin settings |
| Centered card | Auth pages |

## 7. Components (shadcn/ui first)

- Use shadcn/ui primitives for buttons, inputs, selects, dialogs, dropdowns, tooltips, tabs, sheets, badges, cards and tables, restyled with the tokens above.
- Replacing a hand-made component with its shadcn equivalent must keep the same behavior.
- **Buttons:** 32px default, 36px in forms and auth. Variants: primary (one per view), secondary, ghost, destructive.
- **Inputs:** 32 to 36px high, `--border-strong` border, `--primary` focus ring.
- **Badges:** 20px high, caption text, subtle background + strong text of the same color.
- **Tables:** one shared table style (shadcn Table): sticky header, 40px rows, hover row, mono for identifiers, numbers right-aligned. Keep each table's existing features (sorting, paging, actions); don't add new ones.
- **Icons:** lucide-react, 16px in dense UI, 20px in empty states.
- **Shared layout pieces:** `PageHeader` (title, description, existing action buttons) and `EmptyState` (icon, title, one sentence, the existing action if there is one).

## 8. Data display rules

- Every schema identifier (table, column, type, constraint) renders in mono.
- Numbers right-aligned in tables, tabular numerals, thousands separators.
- Long names truncate with ellipsis and show the full name in a tooltip.
- Status values shown as colored badges (section 2), never as plain text.

## 9. AI assistant: the signature look

Restyle the existing assistant where it lives today. Same features, new look.

**Visual signature (assistant only):**
- ✦ mark next to the assistant's name and its messages
- 1px gradient border (`--ai-gradient`) on the assistant header
- `--ai-subtle` background on assistant message cards
- Shimmer on the "thinking" or loading state the assistant already shows

**Message display:**
- Markdown rendered cleanly: headings, lists, tables
- SQL and code in styled code blocks
- If the API already sends reasoning or tool-call steps, show them as a collapsible reasoning block and small step cards; if not, skip them
- Change sets the assistant already creates are shown as a styled card linking to the existing review page
- User and assistant messages clearly different (assistant: left with ✦ and tint; user: right, neutral)
- Prompt input restyled as one clean box with the existing send button

**Component source:** Vercel AI Elements (shadcn-based): Conversation, Message, Prompt Input, Reasoning, Tool, Shimmer, Code Block. Used only for their look; keep the existing data flow and API.

## 10. States

- **Empty states:** use `EmptyState` wherever a page already shows an empty message.
- **Loading:** restyle existing loading indicators as skeletons that match the layout.
- **Errors:** restyle existing error messages: what happened, then what to do.
- Don't add new states the page doesn't have today.

## 11. Copy

- Sentence case, verbs first on buttons ("Add source", "Approve changes").
- No "please", no "successfully", no exclamation marks.
- Only rewrite labels for case and clarity; don't change what they mean.

## 12. Accessibility

- Text contrast 4.5:1, UI contrast 3:1.
- Visible focus ring on every interactive element.
- Icon-only buttons have `aria-label`.
- Use Tailwind logical properties (`ps-`, `pe-`, `ms-`, `me-`) where you touch spacing.

## 13. Definition of done for any UI change

- [ ] Looks only changed; behavior, routes, APIs and data are the same
- [ ] Uses tokens from this file (no raw hex, no arbitrary values)
- [ ] Uses shadcn/ui primitives
- [ ] Works at 1280px and 768px widths
- [ ] Changes only the files the task needs
