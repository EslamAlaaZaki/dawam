"use client";

import { useState } from "react";

import {
  type SnapshotDiff,
  type SnapshotSummary,
  useSnapshotDiff,
} from "../api/snapshots";
import {
  EmptyState,
  ErrorText,
  NativeSelect,
  Panel,
  h4Classes,
  labelClasses,
  listCardClasses,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

type FieldChange = SnapshotDiff["tables"][number]["fields"][number];

function Fields({ fields }: { fields: FieldChange[] }) {
  return (
    <>
      {fields.map((f) => (
        <span key={f.field} className={`${monoClasses} text-muted-foreground`}>
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
    <Panel aria-labelledby="diff-title">
      <h4 id="diff-title" className={h4Classes}>
        Compare Snapshots
      </h4>
      <div className="flex flex-wrap gap-4">
        <label className={`${labelClasses} min-w-56`}>
          From{" "}
          <NativeSelect value={from} onChange={(e) => setFrom(e.target.value)}>
            {options}
          </NativeSelect>
        </label>{" "}
        <label className={`${labelClasses} min-w-56`}>
          To{" "}
          <NativeSelect value={to} onChange={(e) => setTo(e.target.value)}>
            {options}
          </NativeSelect>
        </label>
      </div>
      {diff.isPending && <Loading className="p-0" />}
      {diff.isError && (
        <ErrorText>Could not compare: {diff.error.message}</ErrorText>
      )}
      {empty && <EmptyState>No differences.</EmptyState>}
      {diff.data != null && !empty && (
        <ul
          aria-label="Differences"
          className={`${listCardClasses} [&_li]:px-3 [&_li]:py-2`}
        >
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
                <ul
                  aria-label={`Columns of ${t.name}`}
                  className="mt-1.5 ml-4 flex flex-col gap-1 border-l border-border pl-3 [&_li]:px-0 [&_li]:py-0"
                >
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
    </Panel>
  );
}
