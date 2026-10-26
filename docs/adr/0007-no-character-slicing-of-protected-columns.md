# Protected columns cannot be sliced or pattern-matched piece by piece

Amends ADR 0002.

ADR 0002 masks an output column whenever it derives from a Protected Column, so `SUBSTR(national_id, 1, 3)` came back masked. Masking by lineage is not the whole defence, though: the value check on every result tests whole cells, and a slice small enough (one or two characters, a position, a domain after `@`) passes no validator. A query that pins the slice to something visible (a `CONCAT` with the row key, a derived table, a view the model did not write) would let the model read a protected value piece by piece across queries.

The guard therefore rejects character-slicing and pattern-extraction functions on a Protected Column, in every dialect's spelling, whatever they are nested in: `SUBSTR`, `SUBSTRING`, `LEFT`, `RIGHT`, `MID`, `CHARINDEX`, `INSTR`, `LOCATE`, `STRPOS`/`POSITION`, `PATINDEX`, `STUFF`, `OVERLAY`, `REGEXP_SUBSTR`, `REGEXP_INSTR`, `REGEXP_REPLACE`, `REGEXP_MATCH`, `SPLIT_PART`, `SUBSTRING_INDEX` and Oracle's `SUBSTRB`/`INSTRB` family. Columns that are not protected are unaffected: slicing them stays part of normal exploration. A view whose stored definition slices a protected column is still queryable, its column masked as before: the guard judges the SQL the model writes, and the model cannot choose the view's slice.

`LIKE` and regex predicates on a Protected Column stay rejected outright, as ADR 0002 already has them ("comparing them to literals"). The maintainer's decision for this change also allows them against a per-run read budget per protected column; that relaxes today's rule, so it is left open below rather than implemented here.

Small-cell suppression (k-anonymity) stays rejected, as in ADR 0002.

## Open

- **A per-run read budget for `LIKE`/regex predicates on Protected Columns.** It would let a model check a column's format (Saudi national IDs start with 1, Iqamas with 2) a few times per run, at the cost of a bounded boolean oracle on the value. Today those predicates are rejected, which is stricter. Needs the maintainer's confirmation before the guard is loosened.

## Considered options

- **Keep masking slices by lineage only (ADR 0002 as it was)**: rejected; a slice too small for any validator leaks the value a piece at a time.
- **Reject slicing on every column**: rejected; it would block most legitimate exploration of unflagged columns, which the per-run seconds budget, the row cap and the audit trail already limit (spec §6.8).
- **Small-cell suppression (k-anonymity)**: still rejected as too costly (ADR 0002).
