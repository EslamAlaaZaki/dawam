# Forward-only changes with impact propagation, no revert

Every change to an Asset (Source Schema enhancements, DW Schema tables, mappings, KPIs, files) is forward-only: DAWAM keeps no per-Asset version history and offers no revert, no restore button, no undo of an applied Change Set, and no Baselines. Instead, any structural change (by a user, the AI, regeneration, Snapshot sync or mapping import) lists the impacted Assets found through lineage and offers one transitive Change Set that propagates the change to them, which the user accepts in full or in part. We chose this because reverting one Asset to an old version silently breaks everything that references it (a mapping or KPI pointing at a renamed column), and fixing that through propagation is the same mechanism users need anyway; keeping full history for every Asset would add a large versioning layer for little benefit in Release 1.

## Consequences

- Who-changed-what is kept in audit tables for critical entities (Source Schema enhancements and relationships, DW Schema tables, mappings, KPIs, PII decisions, members and roles, Connections). The audit view shows old values so a user can re-enter them as a new change; it is deliberately not a one-click restore, which would be revert under another name.
- Because nothing can be rolled back, destructive actions are guarded instead: deleting a Source System or DW table is owner-only and soft (`deleted`, marking only that object, never cascading) until an owner confirms, Change Set items that delete or change owner-only settings need an Owner to accept them, and mapping imports go through a Change Set.
- Change Set items store the base values of the fields they change and are skipped as stale if those fields changed since, so a slow AI job can never overwrite a colleague's later edit.
- Skipped or rejected impacts are never lost for good: a "Fix impacts" action recomputes the propagation Change Set from whatever is currently broken.
- Regeneration still needs to know what a user changed by hand. DAWAM stores **generation provenance** per object (the last generated value of each field and which fields the user overrode) and a stable `generation_key`, issued by DAWAM, per generated object. Deleted generated objects keep a tombstone so regeneration never proposes them again. This is not version history: it records only the latest generated value and whether a human overrode it.
- Files are overwritten in place; the AI shows a diff before saving and the user accepts or rejects it.
- The score trend over time is kept; Baselines are not.
