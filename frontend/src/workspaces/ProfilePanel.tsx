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
    <section aria-label="Profile">
      <h5>Profile</h5>
      {unavailable && (
        <p role="note">
          Profiling is unavailable: this Source System was built from a Schema
          Import and has no live Connection to read data from. An owner adds a
          Connection to enable it.
        </p>
      )}
      {canProfile && (
        <div className="form-actions">
          <button
            type="button"
            disabled={start.isPending}
            onClick={() => start.mutate(tableId)}
          >
            Profile this table
          </button>
        </div>
      )}
      {start.isError && (
        <p className="form-error" role="alert">
          {start.error.message}
        </p>
      )}
      {start.isSuccess && (
        <p role="status">Profiling started. Follow it under Background jobs.</p>
      )}
      {canSwitch && profile.isSuccess && (
        <label>
          <input
            type="checkbox"
            checked={profile.data.top_n_enabled}
            disabled={topN.isPending}
            onChange={(event) => topN.mutate(event.target.checked)}
          />{" "}
          Keep the most frequent values of this table (never for protected
          columns)
        </label>
      )}
      {topN.isError && (
        <p className="form-error" role="alert">
          {topN.error.message}
        </p>
      )}
      {profile.isPending && <p>Loading the profile…</p>}
      {profile.isError && (
        <p role="alert">Could not load the profile: {profile.error.message}</p>
      )}
      {profile.isSuccess && profile.data.profiled_at == null && (
        <p className="empty-state">This table has not been profiled yet.</p>
      )}
      {profile.isSuccess && profile.data.profiled_at != null && (
        <>
          <p>
            Sampled rows: {profile.data.row_count}. Profiled{" "}
            {new Date(profile.data.profiled_at).toLocaleString()}.
          </p>
          <table aria-label="Column profiles">
            <thead>
              <tr>
                <th>Column</th>
                <th>Null %</th>
                <th>Distinct</th>
                <th>Min</th>
                <th>Max</th>
                <th>Length (avg / max)</th>
                <th>Patterns</th>
                {profile.data.top_n_enabled && <th>Top values</th>}
              </tr>
            </thead>
            <tbody>
              {profile.data.columns.map((column) => {
                const p = column.profile;
                return (
                  <tr key={column.column_id}>
                    <td>
                      {column.name}
                      {column.is_protected && (
                        <span className="status-badge"> (protected)</span>
                      )}
                    </td>
                    <td>{p ? p.null_pct.toFixed(1) : ""}</td>
                    <td>{p?.distinct_count ?? ""}</td>
                    <td>{p?.min ?? ""}</td>
                    <td>{p?.max ?? ""}</td>
                    <td>
                      {p?.max_len != null
                        ? `${p.avg_len?.toFixed(1)} / ${p.max_len}`
                        : ""}
                    </td>
                    <td>{p?.patterns.join(", ")}</td>
                    {profile.data.top_n_enabled && (
                      <td>
                        {p?.top_values
                          ?.map((v) => `${v.value} (${v.count})`)
                          .join(", ")}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
