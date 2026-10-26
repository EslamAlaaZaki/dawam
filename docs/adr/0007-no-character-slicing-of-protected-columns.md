# Protected columns cannot be sliced or pattern-matched piece by piece

Amends ADR 0002.

ADR 0002 masks an output column whenever it derives from a Protected Column, so `SUBSTR(national_id, 1, 3)` came back masked. Masking by lineage is not the whole defence, though: the value check on every result tests whole cells, and a slice small enough (one or two characters, a position, a domain after `@`) passes no validator. A query that pins the slice to something visible (a `CONCAT` with the row key, a derived table, a view the model did not write) would let the model read a protected value piece by piece across queries.

The guard therefore rejects character-slicing and pattern-extraction functions on a Protected Column, in every dialect's spelling, whatever they are nested in: `SUBSTR`, `SUBSTRING`, `LEFT`, `RIGHT`, `MID`, `CHARINDEX`, `INSTR`, `LOCATE`, `STRPOS`/`POSITION`, `PATINDEX`, `STUFF`, `OVERLAY`, `REGEXP_SUBSTR`, `REGEXP_INSTR`, `REGEXP_REPLACE`, `REGEXP_MATCH`, `SPLIT_PART`, `SUBSTRING_INDEX` and Oracle's `SUBSTRB`/`INSTRB` family. Columns that are not protected are unaffected: slicing them stays part of normal exploration. A view whose stored definition slices a protected column is still queryable, its column masked as before: the guard judges the SQL the model writes, and the model cannot choose the view's slice.

The AI never reads raw PII; over a Protected Column it may only run analytics (counts, duplicate counts, aggregates whose result is not a raw value). Slicing, `LIKE` and regex on it are rejected, and there is no read budget. `LIKE` and regex predicates on a Protected Column stay rejected outright, as ADR 0002 already has them ("comparing them to literals"), and there is no per-run read budget that would let a few through: each one is a yes/no question about the value. Aggregates over a Protected Column are allowed only when the result is not a raw value: `COUNT`, `COUNT(DISTINCT)`, duplicate counts (`COUNT(*) - COUNT(DISTINCT col)`, or a self-join on the column) and `SUM`/`AVG` (returned masked). `MIN`, `MAX` and the other value-emitting aggregates hand back one row's value and stay rejected, as do `GROUP BY` and `ORDER BY` on the column, since many engines return groups in the column's order.

`SUM` or `AVG` over a filter that matches a single row (`WHERE cust_no = 5`) is that row's numeric value. Today the guard returns every `SUM`/`AVG` of a Protected Column masked, so the model does not see it; but any change that shows those aggregates unmasked would reveal one value per single-row filter. That residual risk is accepted, because the only general fix is small-cell suppression (k-anonymity), which ADR 0002 rejects and which stays rejected.

## Considered options

- **Keep masking slices by lineage only (ADR 0002 as it was)**: rejected; a slice too small for any validator leaks the value a piece at a time.
- **Reject slicing on every column**: rejected; it would block most legitimate exploration of unflagged columns, which the per-run seconds budget, the row cap and the audit trail already limit (spec §6.8).
- **A per-run read budget for `LIKE`/regex predicates on Protected Columns**: rejected by the maintainer; it would still let the model read raw PII a little at a time.
- **Small-cell suppression (k-anonymity)**: still rejected as too costly (ADR 0002).
