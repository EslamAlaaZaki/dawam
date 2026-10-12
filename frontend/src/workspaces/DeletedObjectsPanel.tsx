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
import { Button } from "../components/ui/button";
import {
  Actions,
  DetailList,
  ErrorText,
  Hint,
  Panel,
  h3Classes,
  h4Classes,
} from "./parts";
import { Loading } from "../shell/Loading";

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
    return (
      <ErrorText>
        Could not load the deleted list: {deleted.error.message}
      </ErrorText>
    );
  }
  const { systems, tables } = deleted.data;
  if (systems.length === 0 && tables.length === 0) {
    return null;
  }

  return (
    <Panel aria-labelledby="deleted-title">
      <h3 id="deleted-title" className={h3Classes}>
        Deleted
      </h3>
      <Hint>
        These are hidden from the model and the score until you delete them
        permanently.
      </Hint>
      <ul className="flex flex-col gap-2">
        {systems.map((s) => (
          <li
            key={s.id}
            className="flex flex-wrap items-center gap-x-2 gap-y-1"
          >
            Source System {s.name} ({s.code}){" "}
            <Button
              type="button"
              size="sm"
              onClick={() => setSelected({ kind: "system", id: s.id })}
            >
              Review {s.name}
            </Button>
          </li>
        ))}
        {tables.map((t) => (
          <li
            key={t.id}
            className="flex flex-wrap items-center gap-x-2 gap-y-1"
          >
            Table {t.layer}.{t.name}{" "}
            <Button
              type="button"
              size="sm"
              onClick={() => setSelected({ kind: "table", id: t.id })}
            >
              Review {t.name}
            </Button>
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
    </Panel>
  );
}

const reviewClasses =
  "flex flex-col gap-3 rounded-lg border border-sand-5 bg-sand-2 p-4";

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
    return <Loading className="p-0" />;
  }
  if (review.isError) {
    return (
      <ErrorText>
        Could not load what goes with it: {review.error.message}
      </ErrorText>
    );
  }
  const d = review.data;
  return (
    <section
      aria-label={`Permanently delete ${d.system.name}`}
      className={reviewClasses}
    >
      <h4 className={h4Classes}>Permanently delete {d.system.name}</h4>
      <DetailList>
        <dt>Source Objects</dt>
        <dd>
          {d.db_schema_count} schemas, {d.table_count} tables, {d.column_count}{" "}
          columns, {d.routine_count} routines
        </dd>
        <dt>Snapshots</dt>
        <dd>{d.snapshot_count}</dd>
        <dt>Staging Tables and their mappings</dt>
        <dd>{names(d.staging_tables)}</dd>
        <dt>Branches that become broken</dt>
        <dd>
          {d.broken_branches.length
            ? d.broken_branches
                .map((b) => `${b.table_name}: ${b.branch_name}`)
                .join(", ")
            : "none"}
        </dd>
        <dt>Documents and files</dt>
        <dd>
          {d.files.length ? d.files.map((f) => f.path).join(", ") : "none"}
        </dd>
        <dt>KPIs moved to the Data Warehouse</dt>
        <dd>{names(d.rehomed_kpis)}</dd>
        <dt>KPIs deleted</dt>
        <dd>{names(d.deleted_kpis)}</dd>
      </DetailList>
      {purge.isError && <ErrorText>{purge.error.message}</ErrorText>}
      <Actions>
        <Button
          type="button"
          variant="primary"
          disabled={purge.isPending}
          onClick={() => purge.mutate(systemId, { onSuccess: onDone })}
        >
          Delete permanently
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </Actions>
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
    return <Loading className="p-0" />;
  }
  if (review.isError) {
    return (
      <ErrorText>
        Could not load what goes with it: {review.error.message}
      </ErrorText>
    );
  }
  const d = review.data;
  return (
    <section
      aria-label={`Permanently delete ${d.table.name}`}
      className={reviewClasses}
    >
      <h4 className={h4Classes}>Permanently delete {d.table.name}</h4>
      <DetailList>
        <dt>Columns</dt>
        <dd>{d.column_names.length ? d.column_names.join(", ") : "none"}</dd>
        <dt>Its mapping</dt>
        <dd>{d.has_mapping ? "deleted with it" : "none"}</dd>
        <dt>Branches that become broken</dt>
        <dd>
          {d.broken_branches.length
            ? d.broken_branches
                .map((b) => `${b.table_name}: ${b.branch_name}`)
                .join(", ")
            : "none"}
        </dd>
        <dt>KPIs that lose their links</dt>
        <dd>{names(d.unlinked_kpis)}</dd>
      </DetailList>
      {purge.isError && <ErrorText>{purge.error.message}</ErrorText>}
      <Actions>
        <Button
          type="button"
          variant="primary"
          disabled={purge.isPending}
          onClick={() => purge.mutate(tableId, { onSuccess: onDone })}
        >
          Delete permanently
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </Actions>
    </section>
  );
}
