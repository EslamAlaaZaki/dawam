"use client";

import { useState } from "react";

import {
  type SnapshotDiff,
  type SnapshotSummary,
  useSnapshotDiff,
} from "../api/snapshots";

type FieldChange = SnapshotDiff["tables"][number]["fields"][number];

function Fields({ fields }: { fields: FieldChange[] }) {
  return (
    <>
      {fields.map((f) => (
        <span key={f.field}>
          {" "}
          {f.field}: {f.before ?? "none"} to {f.after ?? "none"};
        </span>
      ))}
    </>
  );
}

/**
 * A diff between any two of a Source System's Snapshots (spec story 52): added, removed
 * and changed tables, views, columns and routines, matched by identity.
 */
export function SnapshotDiffView({
  workspaceId,
  systemId,
  snapshots,
}: {
  workspaceId: string;
  systemId: string;
  snapshots: SnapshotSummary[];
}) {
  // Snapshots come newest first: by default compare the latest against the one before it.
  const [from, setFrom] = useState<string>(snapshots[1]?.id ?? "");
  const [to, setTo] = useState<string>(snapshots[0]?.id ?? "");
  const diff = useSnapshotDiff(workspaceId, systemId, from || null, to || null);
  const label = (s: SnapshotSummary) =>
    `${new Date(s.taken_at).toLocaleString()}${s.is_latest ? " (latest)" : ""}`;
  const options = snapshots.map((s) => (
    <option key={s.id} value={s.id}>
      {label(s)}
    </option>
  ));

  if (snapshots.length < 2) {
    return null;
  }
  const empty =
    diff.data != null &&
    diff.data.db_schemas.length +
      diff.data.tables.length +
      diff.data.routines.length ===
      0;
  return (
    <section aria-labelledby="diff-title">
      <h4 id="diff-title">Compare Snapshots</h4>
      <label>
        From{" "}
        <select value={from} onChange={(e) => setFrom(e.target.value)}>
          {options}
        </select>
      </label>{" "}
      <label>
        To{" "}
        <select value={to} onChange={(e) => setTo(e.target.value)}>
          {options}
        </select>
      </label>
      {diff.isPending && <p>Loading…</p>}
      {diff.isError && (
        <p role="alert">Could not compare: {diff.error.message}</p>
      )}
      {empty && <p className="empty-state">No differences.</p>}
      {diff.data != null && !empty && (
        <ul aria-label="Differences">
          {diff.data.db_schemas.map((s) => (
            <li key={s.id}>
              Database Schema {s.name}: {s.change}
              <Fields fields={s.fields} />
            </li>
          ))}
          {diff.data.tables.map((t) => (
            <li key={t.id}>
              {t.kind === "view" ? "View" : "Table"} {t.db_schema}.{t.name}:{" "}
              {t.change}
              <Fields fields={t.fields} />
              {t.columns.length > 0 && (
                <ul aria-label={`Columns of ${t.name}`}>
                  {t.columns.map((c) => (
                    <li key={c.id}>
                      Column {c.name}: {c.change}
                      <Fields fields={c.fields} />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
          {diff.data.routines.map((r) => (
            <li key={r.id}>
              Routine {r.db_schema}.{r.name}
              {r.signature}: {r.change}
              <Fields fields={r.fields} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
