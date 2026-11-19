# Runs store the full result of each tool call, kept with the conversation and redacted wherever shown

The maintainer decided: DAWAM stores the full result of every tool call of an assistant run (including row values from source queries), keeps it as long as the conversation, and sends the last 2 turns' results back with a follow-up question. This changes what ADR 0002 and the persistence rules assumed, where a run kept only a 300-character digest and never row values.

**What is stored.** One row per tool call in `assistant_run_tool_results`, keyed by run and tool call id: the call's name and arguments, the result text exactly as the model received it, the structured columns and rows when the tool returned a table, the row count, the estimated token size, a digest, and a `sent_mode`: `full`, or `digest_over_cap` when the result is over about 4,000 tokens (it is still stored in full). It is plain JSON like messages: conversations are not encrypted either, only secrets are. A separate table keeps the run list light.

**How long.** As long as the conversation: the rows are deleted with the conversation (a member deleting it removes them for good, although the conversation row itself is only marked deleted) and with its Workspace. There is no retention job. Older runs have no stored results, and nothing is backfilled.

**Where it is shown.** Only in the admin run explorer, through `redact_json` / `redact_text`: columns, the first 50 rows and the total row count for a table, otherwise the JSON, plus the token size and the sent mode. Nothing unredacted is shown to a person.

**Where it is sent.** On a new question the assistant sends the tool results of the last 2 turns in full, paired with their calls, quoted as data and inspected for prompt injection again; older turns, and a result over the cap, send their digest. The model gets the values unredacted, as on the first call, and internal-only Workspace rules are unchanged. This is the same in every Workspace, with or without a light model.

## Why

- It is the market standard (OpenAI Agents SDK `ToolOutputTrimmer`, Anthropic context editing, LangGraph trimming, Google ADK compaction): store everything, send recent turns in full, shorten older ones, summarise when needed. DAWAM already shortened and summarised; it now also stores and sends the recent turns in full.
- Follow-ups ("now break that down by month") see the earlier data instead of running the tool again.
- Admins can see what a tool actually returned when monitoring runs.

## Considered options

- **Keep only digests** (before): follow-ups repeat tool calls and admins cannot see results.
- **Grow `assistant_runs.tool_calls`**: rejected; it would make every run list row heavy.
- **Encrypt the results per table**: rejected; messages are not encrypted either, and the data is as sensitive as the conversation that quotes it.
- **A retention job**: not now; the results live and die with the conversation.
- **Always send results in full**: rejected; a result over about 4,000 tokens would crowd out the window, so only its digest goes back.
