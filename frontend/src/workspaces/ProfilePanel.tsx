"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { isActive, useJobs } from "../api/jobs";
import {
  tableProfileKey,
  useImportStatus,
  useSetTopN,
  useStartProfiling,
  useTableProfile,
} from "../api/profiling";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import {
  Actions,
  EmptyState,
  ErrorText,
  TableCard,
  h5Classes,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

/**
 * A table's profile (spec stories 55-57): per column the sampled row count, null %,
 * distinct count, min and max, length and detected patterns, and top values when an owner
 * switched them on for the table. Protected Columns show no min/max or top values. Owners
 * and editors start profiling; only owners switch top-N. A system built from a Schema
 * Import cannot be profiled, and says why.
 */
export function ProfilePanel({
  workspace,
  systemId,
  tableId,
}: {
  workspace: Workspace;
  systemId: string;
  tableId: string;
}) {
  const profile = useTableProfile(workspace.id, systemId, tableId);
  const importStatus = useImportStatus(workspace.id, systemId);
  const start = useStartProfiling(workspace.id, systemId);
  const topN = useSetTopN(workspace.id, systemId, tableId);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = start.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: tableProfileKey(workspace.id, systemId, tableId),
      });
    }
  }, [finished, queryClient, workspace.id, systemId, tableId]);

  const unavailable =
    importStatus.data?.imported === true && !importStatus.data.has_connection;
  const canProfile = allows(workspace, "source_system.profile") && !unavailable;
  const canSwitch = allows(workspace, "source_table.top_n");

  return (
    <section aria-label="Profile" className="flex flex-col gap-3">
      <h5 className={h5Classes}>Profile</h5>
      {unavailable && (
        <p role="note" className="text-muted-foreground">
          Profiling is unavailable: this Source System was built from a Schema
          Import and has no live Connection to read data from. An owner adds a
          Connection to enable it.
        </p>
      )}
      {canProfile && (
        <Actions>
          <Button
            type="button"
            disabled={start.isPending}
            onClick={() =>
              start.mutate(tableId, {
                onSuccess: () =>
                  toast.success(
                    "Profiling started. Follow it under Background jobs.",
                  ),
              })
            }
          >
            Profile this table
          </Button>
        </Actions>
      )}
      {start.isError && <ErrorText>{start.error.message}</ErrorText>}
      {canSwitch && profile.isSuccess && (
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={profile.data.top_n_enabled}
            disabled={topN.isPending}
            onChange={(event) => topN.mutate(event.target.checked)}
          />{" "}
          Keep the most frequent values of this table (never for protected
          columns)
        </label>
      )}
      {topN.isError && <ErrorText>{topN.error.message}</ErrorText>}
      {profile.isPending && (
        <Loading label="Loading the profile…" className="p-0" />
      )}
      {profile.isError && (
        <ErrorText>
          Could not load the profile: {profile.error.message}
        </ErrorText>
      )}
      {profile.isSuccess && profile.data.profiled_at == null && (
        <EmptyState>This table has not been profiled yet.</EmptyState>
      )}
      {profile.isSuccess && profile.data.profiled_at != null && (
        <>
          <p className="text-sm text-muted-foreground">
            Sampled rows: {profile.data.row_count}. Profiled{" "}
            {new Date(profile.data.profiled_at).toLocaleString()}.
          </p>
          <TableCard>
            <Table aria-label="Column profiles">
              <TableHeader>
                <TableRow>
                  <TableHead>Column</TableHead>
                  <TableHead className="text-right">Null %</TableHead>
                  <TableHead className="text-right">Distinct</TableHead>
                  <TableHead>Min</TableHead>
                  <TableHead>Max</TableHead>
                  <TableHead className="text-right">
                    Length (avg / max)
                  </TableHead>
                  <TableHead>Patterns</TableHead>
                  {profile.data.top_n_enabled && (
                    <TableHead>Top values</TableHead>
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {profile.data.columns.map((column) => {
                  const p = column.profile;
                  return (
                    <TableRow key={column.column_id}>
                      <TableCell
                        className={`${monoClasses} max-w-64 truncate`}
                        title={column.name}
                      >
                        {column.name}
                        {column.is_protected && (
                          <span className="font-sans text-sm text-muted-foreground">
                            {" "}
                            (protected)
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p ? p.null_pct.toFixed(1) : ""}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p?.distinct_count ?? ""}
                      </TableCell>
                      <TableCell className={monoClasses}>
                        {p?.min ?? ""}
                      </TableCell>
                      <TableCell className={monoClasses}>
                        {p?.max ?? ""}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p?.max_len != null
                          ? `${p.avg_len?.toFixed(1)} / ${p.max_len}`
                          : ""}
                      </TableCell>
                      <TableCell className={monoClasses}>
                        {p?.patterns.join(", ")}
                      </TableCell>
                      {profile.data.top_n_enabled && (
                        <TableCell className={monoClasses}>
                          {p?.top_values
                            ?.map((v) => `${v.value} (${v.count})`)
                            .join(", ")}
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableCard>
        </>
      )}
    </section>
  );
}
