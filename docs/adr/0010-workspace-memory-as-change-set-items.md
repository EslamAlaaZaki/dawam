# Workspace memory is a Change Set kind, recalled on demand and quoted as data

The maintainer decided: memory items are a new Change Set kind, plain text of at most 500 characters, scoped to one Workspace; saving one is refused when PII is detected (with #385's detector); `recall(query)` returns items quoted as data.

**What an item is.** One short fact the team confirmed, such as "`cust_stat` codes: A=active, S=suspended", a naming convention or a preference. It is plain text, trimmed, 1 to 500 characters, and belongs to one Workspace (deleted with it). The `memory` module owns the `memory_items` table; a row holds the text and when it was accepted, nothing else.

**Saving goes through a Change Set.** The assistant's `remember` tool (chat only, `change_set.review`, so owners and editors) proposes one to ten items as one pending Change Set of origin `ai`, each a `memory_item` create item. A row is written only when a member accepts the item, so everything stored is approved; a pending, rejected or expired item is never recalled. Each proposal gets a scope of its own (`{"kind": "workspace_memory", "proposal": <id>}`), so a second `remember` never supersedes the first. A job does not remember: its Change Set stays one Change Set of design changes.

**No PII.** The text goes through the PII validators of ADR 0002 (`redact_text`: IBANs, emails, ID and Iqama numbers, mobiles, cards, IP addresses and, when NER is on, person names and addresses). The Workspace's own custom PII rules are tried too: their regexes are searched in the text (a regex that runs past its time limit counts as a hit), while their name keywords, which match column names, are not. A built-in rule the Workspace switched off for its column scans still applies to memory. Any hit refuses the item with 422 `invalid_change_set`, naming the rule but never echoing the value. The check runs when the item is proposed and again when it is applied, since the rules or the NER switch may have changed in between. Refusing, not masking: a masked fact ("escalate to [redacted: email]") is useless to recall.

**Recall.** `recall(query)` is a read tool every member's chat and every job is offered (`workspace.view`, metadata level). It ranks the Workspace's approved items by how many of the query's words they contain, newest first on a tie, leaves out those matching none, and returns at most ten; a query without words returns the newest ten. Like every tool result it reaches the model as a `<data>` block and passes the prompt-injection check; its note says the text is untrusted. `GET /workspaces/{id}/memory` lists the approved items for any member. Members review proposed items where Change Sets are reviewed: in the chat and on the Change Set's page.

## Considered options

- **Inject memory into every system prompt**: rejected; it costs tokens on every call and makes memory text look like instructions. On demand, through a tool, it stays data.
- **Mask PII and save the rest**: rejected; see above.
- **Embedding search for recall**: not now; a Workspace holds few items, and word matching works in Arabic and English without an embedding call. It can move to the document index later without changing the tool.
- **Editing or forgetting an item**: not now; changes are forward-only (ADR 0001), and a later ticket can add `update` and `delete` operations to the same handler.
- **Members typing items in directly**: not now; the team confirms facts in a conversation and the assistant proposes them.
