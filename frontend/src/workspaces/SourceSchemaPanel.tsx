"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { dataDictionaryUrl, useSaveDataDictionary } from "../api/files";
import { isActive, useJobs } from "../api/jobs";
import {
  snapshotsKey,
  sourceSchemaKey,
  useSnapshots,
  useStartExtraction,
} from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { SchemaBrowser } from "./SchemaBrowser";
import { SnapshotDiffView } from "./SnapshotDiffView";
import {
  Actions,
  EmptyState,
  ErrorText,
  OkText,
  h3Classes,
  listCardClasses,
} from "./parts";

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
  const canSave = allows(workspace, "file.upload");
  const save = useSaveDataDictionary(workspace.id, system.id);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = extract.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: snapshotsKey(workspace.id, system.id),
      });
      void queryClient.invalidateQueries({
        queryKey: sourceSchemaKey(workspace.id, system.id),
      });
    }
  }, [finished, queryClient, workspace.id, system.id]);

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="folder-title" className={h3Classes}>
          Source Schema
        </h3>
        {canExtract && (
          <Actions className="ml-auto">
            <Button
              type="button"
              variant="primary"
              disabled={extract.isPending}
              onClick={() => extract.mutate()}
            >
              Extract metadata
            </Button>
          </Actions>
        )}
      </div>
      {extract.isError && <ErrorText>{extract.error.message}</ErrorText>}
      {extract.isSuccess && (
        <OkText role="status">
          Extraction started. Follow it under Background jobs; a new Snapshot
          appears here only if something changed.
        </OkText>
      )}
      {snapshots.isPending && <p className="text-sand-11">Loading…</p>}
      {snapshots.isError && (
        <ErrorText>
          Could not load the Snapshots: {snapshots.error.message}
        </ErrorText>
      )}
      {snapshots.isSuccess && snapshots.data.length === 0 && (
        <EmptyState>
          No Snapshot yet. Extract metadata from the Connection.
        </EmptyState>
      )}
      {snapshots.isSuccess && snapshots.data.length > 0 && (
        <Actions>
          <Button asChild>
            <a href={dataDictionaryUrl(workspace.id, system.id)} download>
              Download data dictionary (XLSX)
            </a>
          </Button>
          {canSave && (
            <Button
              type="button"
              disabled={save.isPending}
              onClick={() =>
                save.mutate(undefined, {
                  onSuccess: (file) =>
                    toast.success(`Saved ${file.name} to the file area.`),
                })
              }
            >
              Save data dictionary to files
            </Button>
          )}
        </Actions>
      )}
      {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      {snapshots.isSuccess && snapshots.data.length > 0 && (
        <SchemaBrowser workspace={workspace} systemId={system.id} />
      )}
      {snapshots.isSuccess && snapshots.data.length > 0 && (
        <ul
          aria-label="Snapshots"
          className={`${listCardClasses} [&_li]:px-3 [&_li]:py-2`}
        >
          {snapshots.data.map((snapshot) => (
            <li key={snapshot.id}>
              {new Date(snapshot.taken_at).toLocaleString()}
              {snapshot.is_latest && (
                <>
                  {" "}
                  <Badge variant="accent">Latest</Badge>
                </>
              )}
              {": "}
              {snapshot.schema_count} Database Schemas, {snapshot.table_count}{" "}
              tables and views, {snapshot.column_count} columns,{" "}
              {snapshot.routine_count} routines
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
