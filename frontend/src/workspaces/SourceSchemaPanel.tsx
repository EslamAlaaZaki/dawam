"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { isActive, useJobs } from "../api/jobs";
import { snapshotsKey, sourceSchemaKey, useSnapshots, useStartExtraction } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { SchemaBrowser } from "./SchemaBrowser";
import { SnapshotDiffView } from "./SnapshotDiffView";

/**
 * A Source System's Source Schema (spec stories 45, 46, 54): the browser over its latest
 * Snapshot, its Snapshots newest first, and for owners and editors a button that extracts
 * metadata from the Connection as a background job. Both refresh once that job finishes.
 */
export function SourceSchemaPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canExtract = allows(workspace, "source_system.extract");
  const snapshots = useSnapshots(workspace.id, system.id);
  const extract = useStartExtraction(workspace.id, system.id);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = extract.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({ queryKey: snapshotsKey(workspace.id, system.id) });
      void queryClient.invalidateQueries({ queryKey: sourceSchemaKey(workspace.id, system.id) });
    }
  }, [finished, queryClient, workspace.id, system.id]);

  return (
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">Source Schema</h3>
      {canExtract && (
        <div className="form-actions">
          <button type="button" disabled={extract.isPending} onClick={() => extract.mutate()}>
            Extract metadata
          </button>
        </div>
      )}
      {extract.isError && (
        <p className="form-error" role="alert">
          {extract.error.message}
        </p>
      )}
      {extract.isSuccess && (
        <p role="status">
          Extraction started. Follow it under Background jobs; a new Snapshot appears here only
          if something changed.
        </p>
      )}
      {snapshots.isPending && <p>Loading…</p>}
      {snapshots.isError && (
        <p role="alert">Could not load the Snapshots: {snapshots.error.message}</p>
      )}
      {snapshots.isSuccess && snapshots.data.length === 0 && (
        <p className="empty-state">No Snapshot yet. Extract metadata from the Connection.</p>
      )}
      {snapshots.isSuccess && snapshots.data.length > 0 && (
        <SchemaBrowser workspaceId={workspace.id} systemId={system.id} />
      )}
      {snapshots.isSuccess && snapshots.data.length > 0 && (
        <ul aria-label="Snapshots">
          {snapshots.data.map((snapshot) => (
            <li key={snapshot.id}>
              {new Date(snapshot.taken_at).toLocaleString()}
              {snapshot.is_latest && <strong> Latest</strong>}
              {": "}
              {snapshot.schema_count} Database Schemas, {snapshot.table_count} tables and views,{" "}
              {snapshot.column_count} columns, {snapshot.routine_count} routines
            </li>
          ))}
        </ul>
      )}
      {snapshots.isSuccess && (
        <SnapshotDiffView
          key={snapshots.data.map((s) => s.id).join()}
          workspaceId={workspace.id}
          systemId={system.id}
          snapshots={snapshots.data}
        />
      )}
    </section>
  );
}
