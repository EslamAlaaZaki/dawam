"use client";

import { useState } from "react";

import {
  useDeletedObjects,
  usePurgeDwTable,
  usePurgeSourceSystem,
  useSystemDeletion,
  useTableDeletion,
} from "../api/deletions";
import { allows, type Workspace } from "../api/workspaces";

type Selected = { kind: "system" | "table"; id: string } | null;

function names(items: { name: string }[]): string {
  return items.length ? items.map((i) => i.name).join(", ") : "none";
}

/**
 * The deleted list (spec §7 "Soft deletes"): soft-deleted Source Systems and DW tables, for
 * owners, who review what the permanent delete takes with it before confirming it.
 */
export function DeletedObjectsPanel({ workspace }: { workspace: Workspace }) {
  if (!allows(workspace, "objects.delete")) {
    return null;
  }
  return <DeletedList workspace={workspace} />;
}

function DeletedList({ workspace }: { workspace: Workspace }) {
  const deleted = useDeletedObjects(workspace.id);
  const [selected, setSelected] = useState<Selected>(null);

  if (deleted.isPending) {
    return null;
  }
  if (deleted.isError) {
    return <p role="alert">Could not load the deleted list: {deleted.error.message}</p>;
  }
  const { systems, tables } = deleted.data;
  if (systems.length === 0 && tables.length === 0) {
    return null;
  }

  return (
    <section aria-labelledby="deleted-title">
      <h3 id="deleted-title">Deleted</h3>
      <p>These are hidden from the model and the score until you delete them permanently.</p>
      <ul>
        {systems.map((s) => (
          <li key={s.id}>
            Source System {s.name} ({s.code}){" "}
            <button
              type="button"
              className="secondary"
              onClick={() => setSelected({ kind: "system", id: s.id })}
            >
              Review {s.name}
            </button>
          </li>
        ))}
        {tables.map((t) => (
          <li key={t.id}>
            Table {t.layer}.{t.name}{" "}
            <button
              type="button"
              className="secondary"
              onClick={() => setSelected({ kind: "table", id: t.id })}
            >
              Review {t.name}
            </button>
          </li>
        ))}
      </ul>
      {selected?.kind === "system" && (
        <SystemReview
          workspaceId={workspace.id}
          systemId={selected.id}
          onDone={() => setSelected(null)}
        />
      )}
      {selected?.kind === "table" && (
        <TableReview
          workspaceId={workspace.id}
          tableId={selected.id}
          onDone={() => setSelected(null)}
        />
      )}
    </section>
  );
}

function SystemReview({
  workspaceId,
  systemId,
  onDone,
}: {
  workspaceId: string;
  systemId: string;
  onDone: () => void;
}) {
  const review = useSystemDeletion(workspaceId, systemId);
  const purge = usePurgeSourceSystem(workspaceId);
  if (review.isPending) {
    return <p>Loading…</p>;
  }
  if (review.isError) {
    return <p role="alert">Could not load what goes with it: {review.error.message}</p>;
  }
  const d = review.data;
  return (
    <section aria-label={`Permanently delete ${d.system.name}`}>
      <h4>Permanently delete {d.system.name}</h4>
      <dl className="details">
        <dt>Source Objects</dt>
        <dd>
          {d.db_schema_count} schemas, {d.table_count} tables, {d.column_count} columns,{" "}
          {d.routine_count} routines
        </dd>
        <dt>Snapshots</dt>
        <dd>{d.snapshot_count}</dd>
        <dt>Staging Tables and their mappings</dt>
        <dd>{names(d.staging_tables)}</dd>
        <dt>Branches that become broken</dt>
        <dd>
          {d.broken_branches.length
            ? d.broken_branches.map((b) => `${b.table_name}: ${b.branch_name}`).join(", ")
            : "none"}
        </dd>
        <dt>Documents and files</dt>
        <dd>{d.files.length ? d.files.map((f) => f.path).join(", ") : "none"}</dd>
        <dt>KPIs moved to the Data Warehouse</dt>
        <dd>{names(d.rehomed_kpis)}</dd>
        <dt>KPIs deleted</dt>
        <dd>{names(d.deleted_kpis)}</dd>
      </dl>
      {purge.isError && (
        <p className="form-error" role="alert">
          {purge.error.message}
        </p>
      )}
      <div className="form-actions">
        <button
          type="button"
          disabled={purge.isPending}
          onClick={() => purge.mutate(systemId, { onSuccess: onDone })}
        >
          Delete permanently
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          Cancel
        </button>
      </div>
    </section>
  );
}

function TableReview({
  workspaceId,
  tableId,
  onDone,
}: {
  workspaceId: string;
  tableId: string;
  onDone: () => void;
}) {
  const review = useTableDeletion(workspaceId, tableId);
  const purge = usePurgeDwTable(workspaceId);
  if (review.isPending) {
    return <p>Loading…</p>;
  }
  if (review.isError) {
    return <p role="alert">Could not load what goes with it: {review.error.message}</p>;
  }
  const d = review.data;
  return (
    <section aria-label={`Permanently delete ${d.table.name}`}>
      <h4>Permanently delete {d.table.name}</h4>
      <dl className="details">
        <dt>Columns</dt>
        <dd>{d.column_names.length ? d.column_names.join(", ") : "none"}</dd>
        <dt>Its mapping</dt>
        <dd>{d.has_mapping ? "deleted with it" : "none"}</dd>
        <dt>Branches that become broken</dt>
        <dd>
          {d.broken_branches.length
            ? d.broken_branches.map((b) => `${b.table_name}: ${b.branch_name}`).join(", ")
            : "none"}
        </dd>
        <dt>KPIs that lose their links</dt>
        <dd>{names(d.unlinked_kpis)}</dd>
      </dl>
      {purge.isError && (
        <p className="form-error" role="alert">
          {purge.error.message}
        </p>
      )}
      <div className="form-actions">
        <button
          type="button"
          disabled={purge.isPending}
          onClick={() => purge.mutate(tableId, { onSuccess: onDone })}
        >
          Delete permanently
        </button>
        <button type="button" className="secondary" onClick={onDone}>
          Cancel
        </button>
      </div>
    </section>
  );
}
